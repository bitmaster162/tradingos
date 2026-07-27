from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.rest_client import BinanceRESTClient
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.monitoring.intraday_protection import (
    ADLQuantileSnapshot,
    IntradayProtectionDecision,
    IntradayProtectionStatus,
    IntradayProtectionThresholds,
    QuantitativeRulesSnapshot,
    evaluate_intraday_protection,
    normalize_adl_quantile,
    normalize_api_trading_status,
)
from btcusdt_bot.storage.jsonl import JSONLWriter


@dataclass(slots=True)
class IntradayProtectionDaemonConfig:
    thresholds: IntradayProtectionThresholds
    include_adl: bool = True
    position_mode: str = "ONE_WAY"


class IntradayProtectionDaemon:
    def __init__(
        self,
        config: BotConfig,
        *,
        client: BinanceRESTClient,
        writer: JSONLWriter,
        daemon_config: IntradayProtectionDaemonConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.writer = writer
        self.daemon_config = daemon_config
        self.logger = logger or logging.getLogger("btcusdt_bot.intraday_protection")
        self.status = IntradayProtectionStatus()

    def _evaluate(
        self,
    ) -> tuple[IntradayProtectionDecision, QuantitativeRulesSnapshot, ADLQuantileSnapshot | None, dict[str, object]]:
        quant_payload = self.client.api_trading_status(self.config.symbol).data
        adl_payload = self.client.adl_quantile(self.config.symbol).data if self.daemon_config.include_adl else []
        compared_at_ms = now_ms()
        quant_rules = normalize_api_trading_status(quant_payload, self.config.symbol)
        adl_quantile = None
        if self.daemon_config.include_adl:
            adl_quantile = normalize_adl_quantile(
                adl_payload,
                self.config.symbol,
                position_mode=self.daemon_config.position_mode,
            )
        decision = evaluate_intraday_protection(
            quant_rules=quant_rules,
            adl_quantile=adl_quantile,
            thresholds=self.daemon_config.thresholds,
            compared_at_ms=compared_at_ms,
        )
        return decision, quant_rules, adl_quantile, {
            "api_trading_status": quant_payload,
            "adl_quantile": adl_payload,
        }

    async def run(
        self,
        *,
        interval_seconds: float,
        max_iterations: int | None = None,
    ) -> IntradayProtectionStatus:
        while True:
            self.status.iterations += 1
            try:
                decision, quant_rules, adl_quantile, raw = await asyncio.to_thread(self._evaluate)
                latest_path = self.writer.write_json("live/guards/latest_intraday_protection.json", decision)
                report_path = self.writer.append_record(
                    "reports",
                    f"{self.config.symbol.lower()}_intraday_protection",
                    {
                        "decision": decision,
                        "quant_rules": quant_rules,
                        "adl_quantile": adl_quantile,
                        "raw": raw,
                    },
                    event_time_ms=decision.compared_at_ms,
                )
                self.status.decisions_written += 1
                self.status.last_action = decision.action
                self.status.last_path = str(report_path)
                self.status.latest_quant_utilization = decision.max_quant_utilization
                self.status.latest_adl_quantile = decision.adl_quantile
                if decision.action == "reduce_size":
                    self.status.reduce_size_decisions += 1
                elif decision.action == "observe_only":
                    self.status.observe_only_decisions += 1
                self.writer.write_json("live/guards/latest_intraday_protection_status.json", self.status)
                self.logger.info(
                    "intraday protection decision=%s size_multiplier=%s latest=%s",
                    decision.action,
                    decision.size_multiplier,
                    latest_path,
                )
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.writer.write_json("live/guards/latest_intraday_protection_status.json", self.status)
                self.logger.warning("intraday protection evaluation failed: %s", self.status.last_error)

            if max_iterations is not None and self.status.iterations >= max_iterations:
                return self.status
            await asyncio.sleep(max(0.1, interval_seconds))

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.monitoring.execution_drift import (
    ExecutionBaseline,
    ExecutionDriftDecision,
    ExecutionDriftStatus,
    ExecutionDriftThresholds,
    evaluate_execution_drift,
    load_execution_baseline,
    load_live_execution_payload,
)
from btcusdt_bot.storage.jsonl import JSONLWriter


@dataclass(slots=True)
class ExecutionDriftDaemonConfig:
    baseline_path: Path
    live_report_path: Path
    thresholds: ExecutionDriftThresholds


class ExecutionDriftDaemon:
    def __init__(
        self,
        config: BotConfig,
        *,
        writer: JSONLWriter,
        daemon_config: ExecutionDriftDaemonConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.writer = writer
        self.daemon_config = daemon_config
        self.logger = logger or logging.getLogger("btcusdt_bot.execution_drift")
        self.status = ExecutionDriftStatus()

    def _evaluate(self) -> tuple[ExecutionDriftDecision | None, ExecutionBaseline | None, dict[str, object] | None]:
        baseline = load_execution_baseline(self.daemon_config.baseline_path)
        live_payload = load_live_execution_payload(self.daemon_config.live_report_path)
        compared_at_ms = now_ms()
        decision = evaluate_execution_drift(
            live_payload=live_payload,
            baseline=baseline,
            thresholds=self.daemon_config.thresholds,
            compared_at_ms=compared_at_ms,
        )
        return decision, baseline, live_payload

    async def run(
        self,
        *,
        interval_seconds: float,
        max_iterations: int | None = None,
    ) -> ExecutionDriftStatus:
        while True:
            self.status.iterations += 1
            try:
                decision, baseline, live_payload = await asyncio.to_thread(self._evaluate)
                if decision is None or baseline is None or live_payload is None:
                    raise RuntimeError("drift_evaluation_unavailable")
                latest_path = self.writer.write_json("live/guards/latest_execution_drift.json", decision)
                report_path = self.writer.append_record(
                    "reports",
                    f"{self.config.symbol.lower()}_execution_drift",
                    {"decision": decision, "baseline": baseline, "live_report": live_payload},
                    event_time_ms=decision.compared_at_ms,
                )
                self.status.decisions_written += 1
                self.status.last_action = decision.action
                self.status.last_path = str(report_path)
                if decision.action == "reduce_size":
                    self.status.reduce_size_decisions += 1
                elif decision.action == "observe_only":
                    self.status.observe_only_decisions += 1
                self.writer.write_json("live/guards/latest_execution_drift_status.json", self.status)
                self.logger.info(
                    "execution drift decision=%s size_multiplier=%s latest=%s",
                    decision.action,
                    decision.size_multiplier,
                    latest_path,
                )
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.writer.write_json("live/guards/latest_execution_drift_status.json", self.status)
                self.logger.warning("execution drift evaluation failed: %s", self.status.last_error)

            if max_iterations is not None and self.status.iterations >= max_iterations:
                return self.status
            await asyncio.sleep(max(0.1, interval_seconds))

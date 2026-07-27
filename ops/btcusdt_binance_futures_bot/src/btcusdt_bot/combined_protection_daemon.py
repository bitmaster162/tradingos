from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.monitoring.combined_protection import (
    CombinedProtectionDecision,
    CombinedProtectionState,
    CombinedProtectionStatus,
    CombinedProtectionThresholds,
    evaluate_combined_protection,
    load_combined_protection_state,
)
from btcusdt_bot.monitoring.execution_drift import ExecutionDriftDecision
from btcusdt_bot.monitoring.intraday_protection import IntradayProtectionDecision
from btcusdt_bot.monitoring.pnl_protection import PnLProtectionDecision
from btcusdt_bot.monitoring.trade_reconciliation import TradeReconciliationDecision
from btcusdt_bot.monitoring.session_truth import SessionTruthDecision
from btcusdt_bot.monitoring.session_truth_trend import SessionTruthTrendDecision
from btcusdt_bot.monitoring.economics_regime import EconomicsRegimeDecision
from btcusdt_bot.storage.jsonl import JSONLWriter


@dataclass(slots=True)
class CombinedProtectionDaemonConfig:
    execution_drift_guard_path: Path
    intraday_protection_guard_path: Path
    pnl_protection_guard_path: Path
    trade_reconciliation_guard_path: Path
    state_path: Path
    thresholds: CombinedProtectionThresholds
    session_truth_guard_path: Path | None = None
    session_truth_trend_guard_path: Path | None = None
    economics_regime_guard_path: Path | None = None


class CombinedProtectionDaemon:
    def __init__(
        self,
        config: BotConfig,
        *,
        writer: JSONLWriter,
        daemon_config: CombinedProtectionDaemonConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.writer = writer
        self.daemon_config = daemon_config
        self.logger = logger or logging.getLogger("btcusdt_bot.combined_protection")
        self.status = CombinedProtectionStatus()

    def _load_optional_decision(self, path: Path | None, loader) -> object | None:
        if path is None:
            return None
        try:
            import json
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(payload, dict):
            raise ValueError(f"invalid_guard_payload:{path}")
        return loader(payload)

    def _load_previous_state(self) -> CombinedProtectionState | None:
        try:
            return load_combined_protection_state(self.daemon_config.state_path)
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("combined protection state load failed: %s", exc)
            return None

    def _evaluate(
        self,
    ) -> tuple[
        CombinedProtectionDecision,
        CombinedProtectionState,
        ExecutionDriftDecision | None,
        IntradayProtectionDecision | None,
        PnLProtectionDecision | None,
        TradeReconciliationDecision | None,
        SessionTruthDecision | None,
        SessionTruthTrendDecision | None,
        EconomicsRegimeDecision | None,
    ]:
        execution_drift = self._load_optional_decision(
            self.daemon_config.execution_drift_guard_path,
            ExecutionDriftDecision.from_payload,
        )
        intraday_protection = self._load_optional_decision(
            self.daemon_config.intraday_protection_guard_path,
            IntradayProtectionDecision.from_payload,
        )
        pnl_protection = self._load_optional_decision(
            self.daemon_config.pnl_protection_guard_path,
            PnLProtectionDecision.from_payload,
        )
        trade_reconciliation = self._load_optional_decision(
            self.daemon_config.trade_reconciliation_guard_path,
            TradeReconciliationDecision.from_payload,
        )
        session_truth = self._load_optional_decision(
            self.daemon_config.session_truth_guard_path,
            SessionTruthDecision.from_payload,
        )
        session_truth_trend = self._load_optional_decision(
            self.daemon_config.session_truth_trend_guard_path,
            SessionTruthTrendDecision.from_payload,
        )
        economics_regime = self._load_optional_decision(
            self.daemon_config.economics_regime_guard_path,
            EconomicsRegimeDecision.from_payload,
        )
        if execution_drift is None and intraday_protection is None and pnl_protection is None and trade_reconciliation is None and session_truth is None and session_truth_trend is None and economics_regime is None:
            raise RuntimeError("combined_protection_sources_unavailable")
        previous_state = self._load_previous_state()
        compared_at_ms = now_ms()
        decision, next_state = evaluate_combined_protection(
            execution_drift=execution_drift,
            intraday_protection=intraday_protection,
            pnl_protection=pnl_protection,
            trade_reconciliation=trade_reconciliation,
            session_truth=session_truth,
            session_truth_trend=session_truth_trend,
            economics_regime=economics_regime,
            previous_state=previous_state,
            thresholds=self.daemon_config.thresholds,
            compared_at_ms=compared_at_ms,
        )
        return decision, next_state, execution_drift, intraday_protection, pnl_protection, trade_reconciliation, session_truth, session_truth_trend, economics_regime

    async def run(
        self,
        *,
        interval_seconds: float,
        max_iterations: int | None = None,
    ) -> CombinedProtectionStatus:
        while True:
            self.status.iterations += 1
            try:
                decision, next_state, execution_drift, intraday_protection, pnl_protection, trade_reconciliation, session_truth, session_truth_trend, economics_regime = await asyncio.to_thread(
                    self._evaluate
                )
                latest_path = self.writer.write_json("live/guards/latest_combined_protection.json", decision)
                state_path = self.writer.write_json(self.daemon_config.state_path, next_state)
                report_path = self.writer.append_record(
                    "reports",
                    f"{self.config.symbol.lower()}_combined_protection",
                    {
                        "decision": decision,
                        "state": next_state,
                        "execution_drift": execution_drift,
                        "intraday_protection": intraday_protection,
                        "pnl_protection": pnl_protection,
                        "trade_reconciliation": trade_reconciliation,
                        "session_truth": session_truth,
                        "session_truth_trend": session_truth_trend,
                        "economics_regime": economics_regime,
                    },
                    event_time_ms=decision.compared_at_ms,
                )
                self.status.decisions_written += 1
                self.status.last_action = decision.action
                self.status.last_path = str(report_path)
                self.status.last_cooldown_until_ms = decision.cooldown_until_ms
                if decision.action == "reduce_size":
                    self.status.reduce_size_decisions += 1
                elif decision.action == "observe_only":
                    self.status.observe_only_decisions += 1
                self.writer.write_json("live/guards/latest_combined_protection_status.json", self.status)
                self.logger.info(
                    "combined protection decision=%s size_multiplier=%s latest=%s state=%s",
                    decision.action,
                    decision.size_multiplier,
                    latest_path,
                    state_path,
                )
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.writer.write_json("live/guards/latest_combined_protection_status.json", self.status)
                self.logger.warning("combined protection evaluation failed: %s", self.status.last_error)

            if max_iterations is not None and self.status.iterations >= max_iterations:
                return self.status
            await asyncio.sleep(max(0.1, interval_seconds))

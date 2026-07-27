from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.monitoring.pnl_protection import (
    PnLProtectionDecision,
    PnLProtectionStatus,
    PnLProtectionThresholds,
    PnLSessionAnchor,
    evaluate_pnl_protection,
    extract_session_equity_snapshot,
    load_runtime_state,
    load_session_anchor,
    seed_session_anchor,
    update_session_anchor,
)
from btcusdt_bot.storage.jsonl import JSONLWriter


@dataclass(slots=True)
class PnLProtectionDaemonConfig:
    runtime_state_path: Path
    bootstrap_state_path: Path | None
    anchor_path: Path
    thresholds: PnLProtectionThresholds
    asset: str = "USDT"
    position_side: str = "BOTH"
    reset_anchor_on_new_utc_day: bool = True


class PnLProtectionDaemon:
    def __init__(
        self,
        config: BotConfig,
        *,
        writer: JSONLWriter,
        daemon_config: PnLProtectionDaemonConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.writer = writer
        self.daemon_config = daemon_config
        self.logger = logger or logging.getLogger("btcusdt_bot.pnl_protection")
        self.status = PnLProtectionStatus()

    def _load_anchor(self) -> PnLSessionAnchor | None:
        try:
            return load_session_anchor(self.daemon_config.anchor_path)
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("pnl anchor load failed: %s", exc)
            return None

    def _load_seed_snapshot(self) -> object | None:
        bootstrap_path = self.daemon_config.bootstrap_state_path
        if bootstrap_path is None:
            return None
        try:
            payload = load_runtime_state(bootstrap_path)
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("bootstrap state load failed: %s", exc)
            return None
        return extract_session_equity_snapshot(
            payload,
            symbol=self.config.symbol,
            asset=self.daemon_config.asset,
            position_side=self.daemon_config.position_side,
            source="bootstrap_state",
        )

    def _load_current_snapshot(self) -> object:
        try:
            payload = load_runtime_state(self.daemon_config.runtime_state_path)
            return extract_session_equity_snapshot(
                payload,
                symbol=self.config.symbol,
                asset=self.daemon_config.asset,
                position_side=self.daemon_config.position_side,
                source="runtime_state",
            )
        except FileNotFoundError:
            seed_snapshot = self._load_seed_snapshot()
            if seed_snapshot is None:
                raise
            return seed_snapshot

    def _evaluate(self) -> tuple[PnLProtectionDecision, PnLSessionAnchor, object, object | None]:
        current_snapshot = self._load_current_snapshot()
        anchor = self._load_anchor()
        seed_snapshot = self._load_seed_snapshot()
        if anchor is None:
            anchor = seed_session_anchor(seed_snapshot or current_snapshot)
        anchor = update_session_anchor(
            anchor,
            snapshot=current_snapshot,
            reset_on_new_utc_day=self.daemon_config.reset_anchor_on_new_utc_day,
        )
        compared_at_ms = now_ms()
        decision = evaluate_pnl_protection(
            snapshot=current_snapshot,
            anchor=anchor,
            thresholds=self.daemon_config.thresholds,
            compared_at_ms=compared_at_ms,
        )
        return decision, anchor, current_snapshot, seed_snapshot

    async def run(
        self,
        *,
        interval_seconds: float,
        max_iterations: int | None = None,
    ) -> PnLProtectionStatus:
        while True:
            self.status.iterations += 1
            try:
                decision, anchor, current_snapshot, seed_snapshot = await asyncio.to_thread(self._evaluate)
                self.writer.write_json("live/guards/latest_pnl_protection.json", decision)
                anchor_path = self.writer.write_json(self.daemon_config.anchor_path, anchor)
                report_path = self.writer.append_record(
                    "reports",
                    f"{self.config.symbol.lower()}_pnl_protection",
                    {
                        "decision": decision,
                        "anchor": anchor,
                        "current_snapshot": current_snapshot,
                        "seed_snapshot": seed_snapshot,
                    },
                    event_time_ms=decision.compared_at_ms,
                )
                self.status.decisions_written += 1
                self.status.last_action = decision.action
                self.status.last_path = str(report_path)
                self.status.baseline_equity_usdt = decision.baseline_equity_usdt
                self.status.peak_equity_usdt = decision.peak_equity_usdt
                self.status.current_equity_usdt = decision.current_equity_usdt
                self.status.session_loss_usdt = decision.session_loss_usdt
                self.status.drawdown_usdt = decision.drawdown_usdt
                self.status.unrealized_loss_usdt = decision.unrealized_loss_usdt
                if decision.action == "reduce_size":
                    self.status.reduce_size_decisions += 1
                elif decision.action == "observe_only":
                    self.status.observe_only_decisions += 1
                self.writer.write_json("live/guards/latest_pnl_protection_status.json", self.status)
                self.logger.info(
                    "pnl protection decision=%s size_multiplier=%s anchor=%s",
                    decision.action,
                    decision.size_multiplier,
                    anchor_path,
                )
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.writer.write_json("live/guards/latest_pnl_protection_status.json", self.status)
                self.logger.warning("pnl protection evaluation failed: %s", self.status.last_error)

            if max_iterations is not None and self.status.iterations >= max_iterations:
                return self.status
            await asyncio.sleep(max(0.1, interval_seconds))

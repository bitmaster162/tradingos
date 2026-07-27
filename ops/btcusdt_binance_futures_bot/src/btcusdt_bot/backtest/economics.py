from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.monitoring.economics_regime import (
    EconomicsRegimeDecision,
    EconomicsRegimeThresholds,
    evaluate_economics_regime,
)
from btcusdt_bot.reporting.economics_dashboard import EconomicsDashboard, build_economics_dashboard
from btcusdt_bot.sizing.economics_feedback import (
    EconomicsFeedbackConfig,
    EconomicsFeedbackDecision,
    EconomicsFeedbackPolicy,
)

_ONE = Decimal("1")


@dataclass(slots=True)
class BacktestEconomicsSnapshot:
    dashboard: EconomicsDashboard | None
    regime_decision: EconomicsRegimeDecision | None
    feedback_decision: EconomicsFeedbackDecision
    dashboard_end_date: str = ""


class BacktestEconomicsProvider:
    def __init__(
        self,
        *,
        data_dir: Path,
        symbol: str,
        lookback_days: int = 7,
        economics_feedback_config: EconomicsFeedbackConfig | None = None,
        economics_regime_thresholds: EconomicsRegimeThresholds | None = None,
        enable_economics_feedback: bool = True,
        enable_economics_regime: bool = True,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.symbol = symbol
        self.lookback_days = max(1, int(lookback_days))
        self.enable_economics_feedback = enable_economics_feedback
        self.enable_economics_regime = enable_economics_regime
        self.feedback_policy = EconomicsFeedbackPolicy(economics_feedback_config)
        self.regime_thresholds = economics_regime_thresholds or EconomicsRegimeThresholds()
        self._cache: dict[str, BacktestEconomicsSnapshot] = {}

    def snapshot_for_event(self, *, event_time_ms: int) -> BacktestEconomicsSnapshot:
        event_date = datetime.fromtimestamp(event_time_ms / 1000, tz=UTC).date()
        dashboard_end_date = (event_date - timedelta(days=1)).strftime("%Y-%m-%d")
        if dashboard_end_date in self._cache:
            return self._cache[dashboard_end_date]

        dashboard = build_economics_dashboard(
            data_dir=self.data_dir,
            symbol=self.symbol,
            end_date=dashboard_end_date,
            lookback_days=self.lookback_days,
        )
        if dashboard.active_day_count <= 0:
            snapshot = BacktestEconomicsSnapshot(
                dashboard=None,
                regime_decision=None,
                feedback_decision=EconomicsFeedbackDecision(
                    applied=False,
                    multiplier=_ONE,
                    reason="missing_dashboard",
                ),
                dashboard_end_date=dashboard_end_date,
            )
            self._cache[dashboard_end_date] = snapshot
            return snapshot

        regime_decision: EconomicsRegimeDecision | None = None
        if self.enable_economics_regime:
            regime_decision = evaluate_economics_regime(
                dashboard=dashboard,
                thresholds=self.regime_thresholds,
                compared_at_ms=event_time_ms,
            )

        if not self.enable_economics_feedback:
            feedback_decision = EconomicsFeedbackDecision(
                applied=False,
                multiplier=_ONE,
                reason="disabled",
            )
        elif regime_decision is not None and regime_decision.action != "trade":
            feedback_decision = EconomicsFeedbackDecision(
                applied=False,
                multiplier=_ONE,
                reason="economics_regime_non_trade",
            )
        else:
            feedback_decision = self.feedback_policy.evaluate(dashboard)

        snapshot = BacktestEconomicsSnapshot(
            dashboard=dashboard,
            regime_decision=regime_decision,
            feedback_decision=feedback_decision,
            dashboard_end_date=dashboard_end_date,
        )
        self._cache[dashboard_end_date] = snapshot
        return snapshot

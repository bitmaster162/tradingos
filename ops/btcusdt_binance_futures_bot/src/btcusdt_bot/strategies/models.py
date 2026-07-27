from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from btcusdt_bot.domain.enums import Side


@dataclass(slots=True)
class SignalContext:
    recent_trade_count: int
    buy_aggressor_qty: Decimal
    sell_aggressor_qty: Decimal
    flow_imbalance: Decimal
    last_trade_price: Decimal | None
    mark_trade_divergence_bps: Decimal | None
    funding_rate: Decimal | None
    atr_fraction: Decimal | None = None


@dataclass(slots=True)
class StrategySignal:
    side: Side
    price: Decimal
    atr: Decimal
    reference_level: Decimal
    event_time_ms: int
    context: SignalContext | None = None
    strategy_kind: str = "breakout"
    regime: str = ""
    selected_strategy_kind: str = ""
    preferred_strategy_kind: str = ""

    @property
    def breakout_level(self) -> Decimal:
        return self.reference_level


@dataclass(slots=True)
class SignalEvaluation:
    signal: StrategySignal | None
    context: SignalContext | None
    candidate_side: Side | None = None
    rejection_reason: str = ""
    router_regime: str = ""
    preferred_strategy_kind: str = ""
    selected_strategy_kind: str = ""
    ensemble_breakout_score: Decimal | None = None
    ensemble_reversion_score: Decimal | None = None


class SignalModel(Protocol):
    strategy_kind: str

    def on_agg_trade(
        self,
        *,
        event_time_ms: int,
        price: Decimal,
        qty: Decimal,
        buyer_is_market_maker: bool,
    ) -> SignalContext:
        ...

    def evaluate_price(
        self,
        *,
        event_time_ms: int,
        price: Decimal,
        funding_rate: Decimal | None = None,
    ) -> SignalEvaluation:
        ...

    def on_price(
        self,
        *,
        event_time_ms: int,
        price: Decimal,
        funding_rate: Decimal | None = None,
    ) -> StrategySignal | None:
        ...

    def current_context(
        self,
        *,
        mark_price: Decimal | None,
        funding_rate: Decimal | None,
        event_time_ms: int,
    ) -> SignalContext:
        ...

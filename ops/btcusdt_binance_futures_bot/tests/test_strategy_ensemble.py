from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.strategies import StrategyModelConfig, build_strategy_model


def _ensemble_model():
    return build_strategy_model(
        StrategyModelConfig(
            strategy_kind="ensemble",
            lookback_ticks=3,
            atr_window_ticks=2,
            reversion_entry_atr_multiple=Decimal("0.50"),
            reversion_max_atr_fraction=Decimal("0.0500"),
            router_range_max_atr_fraction=Decimal("0.0060"),
            router_trend_min_atr_fraction=Decimal("0.0100"),
            ensemble_min_observations=3,
        )
    )


def test_strategy_factory_builds_ensemble_model() -> None:
    model = _ensemble_model()
    assert model.strategy_kind == "ensemble"


def test_ensemble_prefers_reversion_in_range_before_online_updates() -> None:
    model = _ensemble_model()

    for idx, price in enumerate((Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")), start=1):
        signal = model.on_price(event_time_ms=idx, price=price)

    assert signal is not None
    assert signal.strategy_kind == "ensemble"
    assert signal.regime == "range"
    assert signal.preferred_strategy_kind == "reversion"
    assert signal.selected_strategy_kind == "reversion"
    assert signal.side == Side.SELL


def test_ensemble_can_override_regime_preference_after_poor_reversion_outcomes() -> None:
    model = _ensemble_model()
    for _ in range(3):
        model.record_entry_outcome(
            strategy_kind="reversion",
            actual_fill_ratio=Decimal("0"),
            fill_ratio_shortfall=Decimal("1"),
            fill_latency_overshoot_seconds=Decimal("3"),
            timed_out=True,
        )
        model.record_trade_outcome(strategy_kind="breakout", net_pnl_bps=Decimal("6"))

    for idx, price in enumerate((Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")), start=1):
        signal = model.on_price(event_time_ms=idx, price=price)

    assert signal is not None
    assert signal.strategy_kind == "ensemble"
    assert signal.regime == "range"
    assert signal.preferred_strategy_kind == "reversion"
    assert signal.selected_strategy_kind == "breakout"
    assert signal.side == Side.BUY

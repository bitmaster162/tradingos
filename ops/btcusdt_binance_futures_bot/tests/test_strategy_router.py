from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.strategies import StrategyModelConfig, build_strategy_model


def test_strategy_factory_builds_router_model() -> None:
    model = build_strategy_model(
        StrategyModelConfig(
            strategy_kind="router",
            lookback_ticks=3,
            atr_window_ticks=2,
            reversion_entry_atr_multiple=Decimal("0.50"),
        )
    )

    assert model.strategy_kind == "router"


def test_router_routes_range_regime_to_reversion_signal() -> None:
    model = build_strategy_model(
        StrategyModelConfig(
            strategy_kind="router",
            lookback_ticks=3,
            atr_window_ticks=2,
            reversion_entry_atr_multiple=Decimal("0.50"),
            reversion_max_atr_fraction=Decimal("0.0500"),
            router_range_max_atr_fraction=Decimal("0.0060"),
            router_trend_min_atr_fraction=Decimal("0.0100"),
        )
    )

    for idx, price in enumerate((Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")), start=1):
        signal = model.on_price(event_time_ms=idx, price=price)

    assert signal is not None
    assert signal.strategy_kind == "router"
    assert signal.selected_strategy_kind == "reversion"
    assert signal.preferred_strategy_kind == "reversion"
    assert signal.regime == "range"
    assert signal.side == Side.SELL


def test_router_routes_trend_regime_to_breakout_signal() -> None:
    model = build_strategy_model(
        StrategyModelConfig(
            strategy_kind="router",
            lookback_ticks=3,
            atr_window_ticks=2,
            reversion_entry_atr_multiple=Decimal("0.50"),
            reversion_max_atr_fraction=Decimal("0.0500"),
            router_range_max_atr_fraction=Decimal("0.0060"),
            router_trend_min_atr_fraction=Decimal("0.0100"),
        )
    )

    for idx, price in enumerate((Decimal("100"), Decimal("101"), Decimal("102"), Decimal("105")), start=1):
        signal = model.on_price(event_time_ms=idx, price=price)

    assert signal is not None
    assert signal.strategy_kind == "router"
    assert signal.selected_strategy_kind == "breakout"
    assert signal.preferred_strategy_kind == "breakout"
    assert signal.regime == "trend"
    assert signal.side == Side.BUY


def test_router_can_fallback_to_breakout_when_preferred_reversion_has_no_signal() -> None:
    model = build_strategy_model(
        StrategyModelConfig(
            strategy_kind="router",
            lookback_ticks=3,
            atr_window_ticks=2,
            reversion_entry_atr_multiple=Decimal("3.0"),
            reversion_max_atr_fraction=Decimal("0.0500"),
            router_range_max_atr_fraction=Decimal("0.0060"),
            router_trend_min_atr_fraction=Decimal("0.0100"),
            router_neutral_preference="reversion",
            router_opportunistic_fallback=True,
        )
    )

    for idx, price in enumerate((Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100.20")), start=1):
        signal = model.on_price(event_time_ms=idx, price=price)

    assert signal is not None
    assert signal.strategy_kind == "router"
    assert signal.selected_strategy_kind == "breakout"
    assert signal.preferred_strategy_kind == "reversion"

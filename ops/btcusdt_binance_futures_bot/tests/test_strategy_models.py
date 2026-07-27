from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.strategies import RollingBreakoutModel, RollingReversionModel, StrategyModelConfig, build_strategy_model


def test_strategy_factory_builds_reversion_model() -> None:
    model = build_strategy_model(
        StrategyModelConfig(
            strategy_kind="reversion",
            lookback_ticks=5,
            atr_window_ticks=3,
            reversion_entry_atr_multiple=Decimal("1.0"),
        )
    )

    assert model.strategy_kind == "reversion"


def test_rolling_reversion_model_emits_sell_signal_on_upper_extension() -> None:
    model = RollingReversionModel(
        lookback_ticks=3,
        atr_window_ticks=2,
        entry_atr_multiple=Decimal("0.50"),
        max_atr_fraction=Decimal("0.0500"),
    )

    assert model.on_price(event_time_ms=1, price=Decimal("100")) is None
    assert model.on_price(event_time_ms=2, price=Decimal("100")) is None
    assert model.on_price(event_time_ms=3, price=Decimal("100")) is None
    signal = model.on_price(event_time_ms=4, price=Decimal("101"))

    assert signal is not None
    assert signal.strategy_kind == "reversion"
    assert signal.side == Side.SELL
    assert signal.reference_level == Decimal("100")


def test_rolling_reversion_model_requires_flow_flip_when_configured() -> None:
    model = RollingReversionModel(
        lookback_ticks=3,
        atr_window_ticks=2,
        entry_atr_multiple=Decimal("0.50"),
        max_atr_fraction=Decimal("0.0500"),
        min_flow_flip=Decimal("0.20"),
    )
    model.on_agg_trade(
        event_time_ms=3,
        price=Decimal("100.9"),
        qty=Decimal("2"),
        buyer_is_market_maker=False,
    )
    model.on_price(event_time_ms=1, price=Decimal("100"))
    model.on_price(event_time_ms=2, price=Decimal("100"))
    model.on_price(event_time_ms=3, price=Decimal("100"))
    evaluation = model.evaluate_price(event_time_ms=4, price=Decimal("101"))

    assert evaluation.signal is None
    assert evaluation.rejection_reason == "reversion_flow_flip_not_confirmed_for_sell"


def test_breakout_and_reversion_models_keep_same_signal_protocol() -> None:
    breakout = RollingBreakoutModel(lookback_ticks=3, atr_window_ticks=2)
    reversion = RollingReversionModel(
        lookback_ticks=3,
        atr_window_ticks=2,
        entry_atr_multiple=Decimal("0.50"),
        max_atr_fraction=Decimal("0.0500"),
    )

    for idx, price in enumerate((Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")), start=1):
        breakout_signal = breakout.on_price(event_time_ms=idx, price=price)
        reversion_signal = reversion.on_price(event_time_ms=idx, price=price)

    assert breakout_signal is not None
    assert reversion_signal is not None
    assert breakout_signal.side == Side.BUY
    assert reversion_signal.side == Side.SELL
    assert breakout_signal.context is not None
    assert reversion_signal.context is not None

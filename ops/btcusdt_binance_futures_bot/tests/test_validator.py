from decimal import Decimal

from btcusdt_bot.domain.models import SymbolFilters
from btcusdt_bot.execution.validator import ExecutionValidator


def test_validate_limit_rounds_down_and_accepts_valid_order() -> None:
    filters = SymbolFilters(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        market_step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        market_min_qty=Decimal("0.001"),
        min_notional=Decimal("5"),
        percent_price_up=Decimal("1.15"),
        percent_price_down=Decimal("0.85"),
        trigger_protect=Decimal("0.15"),
        market_take_bound=Decimal("0.30"),
        max_num_orders=200,
    )
    validator = ExecutionValidator(filters)
    result = validator.validate_limit(
        price=Decimal("65000.19"),
        qty=Decimal("0.0019"),
        reference_price=Decimal("65000"),
    )

    assert result.ok is True
    assert result.normalized_price == Decimal("65000.1")
    assert result.normalized_qty == Decimal("0.001")
    assert result.errors == []


def test_validate_limit_rejects_small_notional() -> None:
    filters = SymbolFilters(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        market_step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        market_min_qty=Decimal("0.001"),
        min_notional=Decimal("5"),
    )
    validator = ExecutionValidator(filters)
    result = validator.validate_limit(
        price=Decimal("1000"),
        qty=Decimal("0.001"),
        reference_price=Decimal("1000"),
    )

    assert result.ok is False
    assert "notional_below_min_notional" in result.errors

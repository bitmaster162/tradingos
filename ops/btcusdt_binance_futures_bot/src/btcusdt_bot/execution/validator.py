from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from btcusdt_bot.domain.models import SymbolFilters, ValidationResult


def decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    units = (value / step).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return units * step


def extract_symbol_filters(exchange_info: dict[str, object], symbol: str) -> SymbolFilters:
    symbols = exchange_info.get("symbols", [])
    row = next((item for item in symbols if item.get("symbol") == symbol), None)
    if row is None:
        raise KeyError(f"Symbol {symbol} not found in exchangeInfo")

    filter_map = {flt["filterType"]: flt for flt in row["filters"]}

    price_filter = filter_map["PRICE_FILTER"]
    lot_size = filter_map["LOT_SIZE"]
    market_lot_size = filter_map["MARKET_LOT_SIZE"]
    min_notional = filter_map["MIN_NOTIONAL"]
    percent_price = filter_map.get("PERCENT_PRICE", {})

    return SymbolFilters(
        symbol=symbol,
        tick_size=decimal(price_filter["tickSize"]),
        step_size=decimal(lot_size["stepSize"]),
        market_step_size=decimal(market_lot_size["stepSize"]),
        min_qty=decimal(lot_size["minQty"]),
        market_min_qty=decimal(market_lot_size["minQty"]),
        min_notional=decimal(min_notional["notional"]),
        percent_price_up=decimal(percent_price["multiplierUp"]) if percent_price else None,
        percent_price_down=decimal(percent_price["multiplierDown"]) if percent_price else None,
        trigger_protect=decimal(row["triggerProtect"]) if row.get("triggerProtect") is not None else None,
        market_take_bound=decimal(row["marketTakeBound"]) if row.get("marketTakeBound") is not None else None,
        max_num_orders=int(filter_map["MAX_NUM_ORDERS"]["limit"]) if "MAX_NUM_ORDERS" in filter_map else None,
    )


class ExecutionValidator:
    def __init__(self, filters: SymbolFilters):
        self.filters = filters

    def validate_limit(
        self,
        *,
        price: Decimal,
        qty: Decimal,
        reference_price: Decimal | None = None,
    ) -> ValidationResult:
        price = floor_to_step(decimal(price), self.filters.tick_size)
        qty = floor_to_step(decimal(qty), self.filters.step_size)

        errors: list[str] = []
        warnings: list[str] = []

        if qty < self.filters.min_qty:
            errors.append("qty_below_min_qty")

        if price <= 0:
            errors.append("price_non_positive")

        notional = price * qty
        if notional < self.filters.min_notional:
            errors.append("notional_below_min_notional")

        if reference_price is not None:
            ref = decimal(reference_price)
            if self.filters.percent_price_up is not None and price > ref * self.filters.percent_price_up:
                errors.append("price_above_percent_price_cap")
            if self.filters.percent_price_down is not None and price < ref * self.filters.percent_price_down:
                errors.append("price_below_percent_price_floor")

        if qty == 0:
            errors.append("qty_rounded_to_zero")
        if price == 0:
            errors.append("price_rounded_to_zero")

        if not errors and reference_price is not None:
            distance = abs(price - decimal(reference_price)) / decimal(reference_price)
            if distance > Decimal("0.01"):
                warnings.append("limit_far_from_reference_gt_1pct")

        return ValidationResult(
            ok=not errors,
            normalized_price=price,
            normalized_qty=qty,
            notional=notional,
            errors=errors,
            warnings=warnings,
        )

    def validate_market(
        self,
        *,
        qty: Decimal,
        mark_price: Decimal,
    ) -> ValidationResult:
        qty = floor_to_step(decimal(qty), self.filters.market_step_size)
        mark_price = decimal(mark_price)

        errors: list[str] = []
        warnings: list[str] = []

        if qty < self.filters.market_min_qty:
            errors.append("qty_below_market_min_qty")

        if qty == 0:
            errors.append("qty_rounded_to_zero")

        notional = qty * mark_price
        if notional < self.filters.min_notional:
            errors.append("notional_below_min_notional")

        if self.filters.market_take_bound is not None:
            warnings.append(f"market_take_bound={self.filters.market_take_bound}")

        return ValidationResult(
            ok=not errors,
            normalized_price=None,
            normalized_qty=qty,
            notional=notional,
            errors=errors,
            warnings=warnings,
        )

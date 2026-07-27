from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.simulator.depth_book import DepthBookSnapshot


_ZERO = Decimal("0")
_ONE = Decimal("1")
_BPS_DENOM = Decimal("10000")


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        return _ZERO
    return sum(values, start=_ZERO) / Decimal(len(values))


@dataclass(slots=True)
class DepthSweepEstimate:
    side: Side
    qty: Decimal
    filled_qty: Decimal
    avg_price: Decimal | None
    best_price: Decimal | None
    sweep_slippage_bps: Decimal | None
    displayed_coverage_ratio: Decimal
    levels_consumed: int
    used_tail: bool = False
    used_synthetic_tail: bool = False
    synthetic_tail_qty: Decimal = _ZERO
    synthetic_tail_coverage_ratio: Decimal = _ZERO
    synthetic_tail_levels_consumed: int = 0
    terminal_tail_qty: Decimal = _ZERO
    terminal_tail_ratio: Decimal = _ZERO


@dataclass(slots=True)
class DepthLiquidityConfig:
    enabled: bool = True
    min_displayed_coverage_ratio: Decimal | None = Decimal("0.75")
    max_sweep_slippage_bps: Decimal | None = Decimal("3.0")
    tail_penalty_bps: Decimal = Decimal("5.0")
    synthetic_tail_levels: int = 3
    synthetic_tail_replenishment_ratio: Decimal = Decimal("0.50")
    synthetic_tail_step_bps: Decimal = Decimal("1.0")
    synthetic_tail_reference_levels: int = 3


@dataclass(slots=True)
class DepthLiquidityDecision:
    execute: bool
    estimate: DepthSweepEstimate | None
    reason: str = ""


class DepthSweepExecutionModel:
    def __init__(
        self,
        *,
        tail_penalty_bps: Decimal = Decimal("5.0"),
        synthetic_tail_levels: int = 3,
        synthetic_tail_replenishment_ratio: Decimal = Decimal("0.50"),
        synthetic_tail_step_bps: Decimal = Decimal("1.0"),
        synthetic_tail_reference_levels: int = 3,
    ) -> None:
        self.tail_penalty_bps = max(_ZERO, tail_penalty_bps)
        self.synthetic_tail_levels = max(0, synthetic_tail_levels)
        self.synthetic_tail_replenishment_ratio = max(_ZERO, synthetic_tail_replenishment_ratio)
        self.synthetic_tail_step_bps = max(_ZERO, synthetic_tail_step_bps)
        self.synthetic_tail_reference_levels = max(1, synthetic_tail_reference_levels)

    def estimate(self, *, side: Side, qty: Decimal, book: DepthBookSnapshot | None) -> DepthSweepEstimate | None:
        qty = max(_ZERO, qty)
        if qty <= 0 or book is None:
            return None

        levels = book.bids if side == Side.SELL else book.asks
        if not levels:
            return DepthSweepEstimate(
                side=side,
                qty=qty,
                filled_qty=_ZERO,
                avg_price=None,
                best_price=None,
                sweep_slippage_bps=None,
                displayed_coverage_ratio=_ZERO,
                levels_consumed=0,
                used_tail=False,
                used_synthetic_tail=False,
                synthetic_tail_qty=_ZERO,
                synthetic_tail_coverage_ratio=_ZERO,
                synthetic_tail_levels_consumed=0,
                terminal_tail_qty=_ZERO,
                terminal_tail_ratio=_ZERO,
            )

        remaining = qty
        displayed_notional = _ZERO
        displayed_filled_qty = _ZERO
        levels_consumed = 0
        for level in levels:
            if remaining <= 0:
                break
            level_fill_qty = min(remaining, max(_ZERO, level.qty))
            if level_fill_qty <= 0:
                continue
            displayed_notional += level.price * level_fill_qty
            displayed_filled_qty += level_fill_qty
            remaining -= level_fill_qty
            levels_consumed += 1

        displayed_coverage_ratio = min(_ONE, displayed_filled_qty / qty) if qty > 0 else _ZERO
        best_price = levels[0].price if levels else None
        total_notional = displayed_notional
        filled_qty = displayed_filled_qty
        used_tail = False
        used_synthetic_tail = False
        synthetic_tail_qty = _ZERO
        synthetic_tail_levels_consumed = 0
        terminal_tail_qty = _ZERO

        reference_price = levels[min(max(levels_consumed - 1, 0), len(levels) - 1)].price
        reference_level_slice = levels[-self.synthetic_tail_reference_levels :]
        reference_qty = _mean([max(_ZERO, level.qty) for level in reference_level_slice])
        current_synthetic_level_qty = reference_qty * self.synthetic_tail_replenishment_ratio

        if (
            remaining > 0
            and best_price is not None
            and best_price > 0
            and self.synthetic_tail_levels > 0
            and self.synthetic_tail_replenishment_ratio > 0
            and current_synthetic_level_qty > 0
        ):
            used_tail = True
            used_synthetic_tail = True
            for synthetic_idx in range(1, self.synthetic_tail_levels + 1):
                if remaining <= 0:
                    break
                level_qty = max(_ZERO, current_synthetic_level_qty)
                if level_qty <= 0:
                    break
                penalty_bps = self.tail_penalty_bps + self.synthetic_tail_step_bps * Decimal(synthetic_idx - 1)
                if side == Side.SELL:
                    synthetic_price = max(_ZERO, reference_price * (_ONE - penalty_bps / _BPS_DENOM))
                else:
                    synthetic_price = reference_price * (_ONE + penalty_bps / _BPS_DENOM)
                level_fill_qty = min(remaining, level_qty)
                if level_fill_qty <= 0:
                    break
                total_notional += synthetic_price * level_fill_qty
                synthetic_tail_qty += level_fill_qty
                filled_qty += level_fill_qty
                remaining -= level_fill_qty
                synthetic_tail_levels_consumed = synthetic_idx
                current_synthetic_level_qty = level_qty * self.synthetic_tail_replenishment_ratio

        if remaining > 0 and best_price is not None and best_price > 0:
            used_tail = True
            terminal_tail_qty = remaining
            terminal_penalty_multiplier = Decimal(max(synthetic_tail_levels_consumed, 1))
            penalty_bps = self.tail_penalty_bps + self.synthetic_tail_step_bps * terminal_penalty_multiplier
            if side == Side.SELL:
                tail_price = max(_ZERO, reference_price * (_ONE - penalty_bps / _BPS_DENOM))
            else:
                tail_price = reference_price * (_ONE + penalty_bps / _BPS_DENOM)
            total_notional += tail_price * remaining
            filled_qty = qty
            remaining = _ZERO

        avg_price = total_notional / qty if qty > 0 and filled_qty > 0 else None
        synthetic_tail_coverage_ratio = min(_ONE, synthetic_tail_qty / qty) if qty > 0 else _ZERO
        terminal_tail_ratio = min(_ONE, terminal_tail_qty / qty) if qty > 0 else _ZERO
        sweep_slippage_bps: Decimal | None
        if avg_price is None or best_price is None or best_price <= 0:
            sweep_slippage_bps = None
        elif side == Side.SELL:
            sweep_slippage_bps = max(_ZERO, (best_price - avg_price) / best_price * _BPS_DENOM)
        else:
            sweep_slippage_bps = max(_ZERO, (avg_price - best_price) / best_price * _BPS_DENOM)

        return DepthSweepEstimate(
            side=side,
            qty=qty,
            filled_qty=filled_qty,
            avg_price=avg_price,
            best_price=best_price,
            sweep_slippage_bps=sweep_slippage_bps,
            displayed_coverage_ratio=displayed_coverage_ratio,
            levels_consumed=levels_consumed,
            used_tail=used_tail,
            used_synthetic_tail=used_synthetic_tail,
            synthetic_tail_qty=synthetic_tail_qty,
            synthetic_tail_coverage_ratio=synthetic_tail_coverage_ratio,
            synthetic_tail_levels_consumed=synthetic_tail_levels_consumed,
            terminal_tail_qty=terminal_tail_qty,
            terminal_tail_ratio=terminal_tail_ratio,
        )


class DepthLiquidityPolicy:
    def __init__(self, config: DepthLiquidityConfig | None = None) -> None:
        self.config = config or DepthLiquidityConfig()
        self.sweep_model = DepthSweepExecutionModel(
            tail_penalty_bps=self.config.tail_penalty_bps,
            synthetic_tail_levels=self.config.synthetic_tail_levels,
            synthetic_tail_replenishment_ratio=self.config.synthetic_tail_replenishment_ratio,
            synthetic_tail_step_bps=self.config.synthetic_tail_step_bps,
            synthetic_tail_reference_levels=self.config.synthetic_tail_reference_levels,
        )

    @staticmethod
    def exit_side_for_entry(entry_side: Side) -> Side:
        return Side.SELL if entry_side == Side.BUY else Side.BUY

    def evaluate_for_entry(
        self,
        *,
        entry_side: Side,
        qty: Decimal,
        book: DepthBookSnapshot | None,
    ) -> DepthLiquidityDecision:
        if not self.config.enabled:
            estimate = self.sweep_model.estimate(side=self.exit_side_for_entry(entry_side), qty=qty, book=book)
            return DepthLiquidityDecision(execute=True, estimate=estimate)
        estimate = self.sweep_model.estimate(side=self.exit_side_for_entry(entry_side), qty=qty, book=book)
        if estimate is None:
            return DepthLiquidityDecision(execute=True, estimate=None)
        if estimate.avg_price is None:
            return DepthLiquidityDecision(execute=False, estimate=estimate, reason="no_exit_depth")
        if (
            self.config.min_displayed_coverage_ratio is not None
            and estimate.displayed_coverage_ratio < self.config.min_displayed_coverage_ratio
        ):
            return DepthLiquidityDecision(
                execute=False,
                estimate=estimate,
                reason="insufficient_exit_depth_coverage",
            )
        if (
            self.config.max_sweep_slippage_bps is not None
            and estimate.sweep_slippage_bps is not None
            and estimate.sweep_slippage_bps > self.config.max_sweep_slippage_bps
        ):
            return DepthLiquidityDecision(
                execute=False,
                estimate=estimate,
                reason="exit_depth_sweep_too_costly",
            )
        return DepthLiquidityDecision(execute=True, estimate=estimate)

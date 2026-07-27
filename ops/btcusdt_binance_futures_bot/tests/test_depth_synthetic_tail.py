from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.simulator.depth_book import DepthBookSnapshot, DepthLevel
from btcusdt_bot.simulator.depth_liquidity import DepthSweepExecutionModel


def _book() -> DepthBookSnapshot:
    return DepthBookSnapshot(
        event_time_ms=1000,
        transaction_time_ms=999,
        last_update_id=101,
        levels=2,
        bids=[
            DepthLevel(Decimal("100.0"), Decimal("1.0")),
            DepthLevel(Decimal("99.9"), Decimal("1.5")),
        ],
        asks=[
            DepthLevel(Decimal("100.1"), Decimal("0.8")),
            DepthLevel(Decimal("100.2"), Decimal("1.2")),
        ],
    )


def test_depth_sweep_uses_synthetic_tail_before_terminal_fallback() -> None:
    model = DepthSweepExecutionModel(
        tail_penalty_bps=Decimal("5.0"),
        synthetic_tail_levels=2,
        synthetic_tail_replenishment_ratio=Decimal("1.0"),
        synthetic_tail_step_bps=Decimal("1.0"),
        synthetic_tail_reference_levels=2,
    )

    estimate = model.estimate(side=Side.SELL, qty=Decimal("4.0"), book=_book())

    assert estimate is not None
    assert estimate.displayed_coverage_ratio == Decimal("0.625")
    assert estimate.synthetic_tail_coverage_ratio == Decimal("0.375")
    assert estimate.synthetic_tail_qty == Decimal("1.5")
    assert estimate.synthetic_tail_levels_consumed == 2
    assert estimate.terminal_tail_ratio == Decimal("0")
    assert estimate.used_synthetic_tail is True
    assert estimate.used_tail is True
    assert estimate.avg_price is not None
    assert estimate.sweep_slippage_bps is not None


def test_depth_sweep_falls_back_to_terminal_tail_when_synthetic_tail_is_exhausted() -> None:
    model = DepthSweepExecutionModel(
        tail_penalty_bps=Decimal("5.0"),
        synthetic_tail_levels=1,
        synthetic_tail_replenishment_ratio=Decimal("0.50"),
        synthetic_tail_step_bps=Decimal("1.0"),
        synthetic_tail_reference_levels=2,
    )

    estimate = model.estimate(side=Side.SELL, qty=Decimal("4.0"), book=_book())

    assert estimate is not None
    assert estimate.synthetic_tail_qty > Decimal("0")
    assert estimate.terminal_tail_qty > Decimal("0")
    assert estimate.terminal_tail_ratio > Decimal("0")
    assert estimate.used_tail is True

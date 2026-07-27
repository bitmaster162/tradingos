from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.simulator.depth_book import DepthBookSnapshot, DepthLevel
from btcusdt_bot.simulator.depth_liquidity import DepthLiquidityConfig, DepthLiquidityPolicy, DepthSweepExecutionModel


def _book() -> DepthBookSnapshot:
    return DepthBookSnapshot(
        event_time_ms=1000,
        transaction_time_ms=999,
        last_update_id=101,
        levels=3,
        bids=[
            DepthLevel(Decimal("100.0"), Decimal("1.0")),
            DepthLevel(Decimal("99.9"), Decimal("1.5")),
        ],
        asks=[
            DepthLevel(Decimal("100.1"), Decimal("0.8")),
            DepthLevel(Decimal("100.2"), Decimal("1.2")),
        ],
    )


def test_depth_sweep_execution_model_sweeps_multiple_levels() -> None:
    estimate = DepthSweepExecutionModel().estimate(side=Side.SELL, qty=Decimal("2.0"), book=_book())

    assert estimate is not None
    assert estimate.avg_price == Decimal("99.95")
    assert estimate.displayed_coverage_ratio == Decimal("1")
    assert estimate.levels_consumed == 2
    assert estimate.sweep_slippage_bps == Decimal("5")


def test_depth_liquidity_policy_rejects_when_displayed_coverage_is_too_low() -> None:
    policy = DepthLiquidityPolicy(
        DepthLiquidityConfig(
            min_displayed_coverage_ratio=Decimal("0.80"),
            max_sweep_slippage_bps=Decimal("10.0"),
            tail_penalty_bps=Decimal("5.0"),
        )
    )

    decision = policy.evaluate_for_entry(entry_side=Side.BUY, qty=Decimal("4.0"), book=_book())

    assert decision.execute is False
    assert decision.reason == "insufficient_exit_depth_coverage"
    assert decision.estimate is not None
    assert decision.estimate.displayed_coverage_ratio < Decimal("0.80")
    assert decision.estimate.used_tail is True

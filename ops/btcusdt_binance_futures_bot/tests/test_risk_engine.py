from decimal import Decimal

from btcusdt_bot.risk.engine import RiskContext, RiskLimits


def _context(**overrides: object) -> RiskContext:
    values: dict[str, object] = {
        "mark_price": Decimal("100"),
        "current_position_qty": Decimal("0"),
        "current_leverage": Decimal("1"),
        "realized_pnl_today": Decimal("0"),
        "open_normal_orders": 0,
        "open_algo_orders": 0,
        "last_market_data_age_ms": 0,
    }
    values.update(overrides)
    return RiskContext(**values)


def test_reconcile_guard_is_not_required_for_dry_run_context() -> None:
    decision = RiskLimits().evaluate_new_entry(proposed_qty=Decimal("1"), ctx=_context())

    assert decision.allow_new_entry is True


def test_reconcile_guard_requires_explicit_freshness_budget_for_order_sending() -> None:
    decision = RiskLimits().evaluate_new_entry(
        proposed_qty=Decimal("1"),
        ctx=_context(reconcile_required=True, reconcile_age_ms=0, reconcile_mismatch_count=0),
    )

    assert decision.allow_new_entry is False
    assert "reconcile_freshness_limit_missing" in decision.hard_reasons


def test_reconcile_guard_rejects_missing_stale_and_divergent_state() -> None:
    limits = RiskLimits(stale_reconcile_limit_ms=5_000)

    missing = limits.evaluate_new_entry(
        proposed_qty=Decimal("1"),
        ctx=_context(reconcile_required=True),
    )
    stale = limits.evaluate_new_entry(
        proposed_qty=Decimal("1"),
        ctx=_context(reconcile_required=True, reconcile_age_ms=5_001, reconcile_mismatch_count=0),
    )
    divergent = limits.evaluate_new_entry(
        proposed_qty=Decimal("1"),
        ctx=_context(reconcile_required=True, reconcile_age_ms=1, reconcile_mismatch_count=2),
    )

    assert "reconcile_state_missing" in missing.hard_reasons
    assert "reconcile_health_missing" in missing.hard_reasons
    assert "stale_reconcile_state" in stale.hard_reasons
    assert "reconcile_state_divergence" in divergent.hard_reasons


def test_reconcile_guard_allows_fresh_zero_mismatch_state() -> None:
    decision = RiskLimits(stale_reconcile_limit_ms=5_000).evaluate_new_entry(
        proposed_qty=Decimal("1"),
        ctx=_context(reconcile_required=True, reconcile_age_ms=5_000, reconcile_mismatch_count=0),
    )

    assert decision.allow_new_entry is True
    assert decision.hard_reasons == []

from decimal import Decimal

from btcusdt_bot.monitoring.pnl_protection import (
    SessionEquitySnapshot,
    evaluate_pnl_protection,
    extract_session_equity_snapshot,
    seed_session_anchor,
    update_session_anchor,
)


def test_extract_session_equity_snapshot_reads_serialized_runtime_state() -> None:
    payload = {
        "account": {
            "balances": {"USDT": "1000"},
            "positions": {
                "BTCUSDT/BOTH": {
                    "amount": "0.015",
                    "unrealized_pnl": "-7.5",
                }
            },
            "last_event_time_ms": 123456,
        }
    }

    snapshot = extract_session_equity_snapshot(payload, symbol="BTCUSDT")

    assert snapshot.wallet_balance_usdt == Decimal("1000")
    assert snapshot.unrealized_pnl_usdt == Decimal("-7.5")
    assert snapshot.position_qty == Decimal("0.015")
    assert snapshot.estimated_equity_usdt == Decimal("992.5")
    assert snapshot.event_time_ms == 123456


def test_pnl_protection_observe_only_on_large_loss_and_drawdown() -> None:
    baseline = SessionEquitySnapshot(
        symbol="BTCUSDT",
        asset="USDT",
        source="bootstrap_state",
        event_time_ms=1_700_000_000_000,
        wallet_balance_usdt=Decimal("1000"),
        unrealized_pnl_usdt=Decimal("0"),
        position_qty=Decimal("0"),
        estimated_equity_usdt=Decimal("1000"),
    )
    anchor = seed_session_anchor(baseline)

    peak = SessionEquitySnapshot(
        symbol="BTCUSDT",
        asset="USDT",
        source="runtime_state",
        event_time_ms=1_700_000_100_000,
        wallet_balance_usdt=Decimal("1020"),
        unrealized_pnl_usdt=Decimal("0"),
        position_qty=Decimal("0"),
        estimated_equity_usdt=Decimal("1020"),
    )
    anchor = update_session_anchor(anchor, snapshot=peak)

    current = SessionEquitySnapshot(
        symbol="BTCUSDT",
        asset="USDT",
        source="runtime_state",
        event_time_ms=1_700_000_200_000,
        wallet_balance_usdt=Decimal("975"),
        unrealized_pnl_usdt=Decimal("-10"),
        position_qty=Decimal("0.01"),
        estimated_equity_usdt=Decimal("965"),
    )
    anchor = update_session_anchor(anchor, snapshot=current)

    decision = evaluate_pnl_protection(snapshot=current, anchor=anchor, compared_at_ms=999)

    assert decision.action == "observe_only"
    assert decision.size_multiplier == Decimal("0")
    assert decision.session_loss_usdt == Decimal("35")
    assert decision.drawdown_usdt == Decimal("55")
    assert decision.unrealized_loss_usdt == Decimal("10")
    assert "session_loss_fraction_above_observe_threshold" in decision.reasons
    assert decision.compared_at_ms == 999


def test_pnl_anchor_resets_on_new_utc_day() -> None:
    day_one = SessionEquitySnapshot(
        symbol="BTCUSDT",
        asset="USDT",
        source="bootstrap_state",
        event_time_ms=1_700_006_400_000,
        wallet_balance_usdt=Decimal("1000"),
        unrealized_pnl_usdt=Decimal("0"),
        position_qty=Decimal("0"),
        estimated_equity_usdt=Decimal("1000"),
    )
    anchor = seed_session_anchor(day_one)

    day_two = SessionEquitySnapshot(
        symbol="BTCUSDT",
        asset="USDT",
        source="runtime_state",
        event_time_ms=1_700_092_800_000,
        wallet_balance_usdt=Decimal("990"),
        unrealized_pnl_usdt=Decimal("0"),
        position_qty=Decimal("0"),
        estimated_equity_usdt=Decimal("990"),
    )

    updated = update_session_anchor(anchor, snapshot=day_two, reset_on_new_utc_day=True)

    assert updated.baseline_equity_usdt == Decimal("990")
    assert updated.peak_equity_usdt == Decimal("990")
    assert updated.started_at_ms == day_two.event_time_ms

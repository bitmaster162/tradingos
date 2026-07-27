from decimal import Decimal

from btcusdt_bot.monitoring.session_truth import SessionTruthThresholds, evaluate_session_truth


def test_session_truth_keeps_trade_when_sample_not_ready() -> None:
    decision = evaluate_session_truth(
        exchange_user_trades=[
            {"orderId": 1, "quoteQty": "100", "realizedPnl": "-5", "commission": "0.5", "maker": False},
        ],
        exchange_income_rows=[{"incomeType": "FUNDING_FEE", "income": "-0.2"}],
        lookback_start_ms=1,
        lookback_end_ms=2,
        thresholds=SessionTruthThresholds(min_exchange_trade_count=3, min_quote_qty_usdt=Decimal("1000")),
        compared_at_ms=3,
    )

    assert decision.sample_ready is False
    assert decision.action == "trade"
    assert decision.net_realized_pnl_usdt == Decimal("-5.7")



def test_session_truth_observes_on_bad_session_economics() -> None:
    user_trades = [
        {"id": 1, "orderId": 10, "quoteQty": "1000", "realizedPnl": "0.50", "commission": "1.20", "maker": False},
        {"id": 2, "orderId": 11, "quoteQty": "1000", "realizedPnl": "0.25", "commission": "1.20", "maker": False},
        {"id": 3, "orderId": 12, "quoteQty": "1000", "realizedPnl": "0.25", "commission": "1.20", "maker": False},
        {"id": 4, "orderId": 13, "quoteQty": "1000", "realizedPnl": "0.00", "commission": "1.20", "maker": False},
    ]
    income_rows = [
        {"incomeType": "FUNDING_FEE", "income": "-1.00"},
    ]

    decision = evaluate_session_truth(
        exchange_user_trades=user_trades,
        exchange_income_rows=income_rows,
        lookback_start_ms=1,
        lookback_end_ms=2,
        compared_at_ms=3,
    )

    assert decision.sample_ready is True
    assert decision.action == "observe_only"
    assert decision.exchange_trade_count == 4
    assert decision.maker_ratio == Decimal("0")
    assert decision.net_realized_pnl_usdt == Decimal("-4.80")
    assert decision.net_realized_bps == Decimal("-12.0000")
    assert "net_realized_bps_below_observe_threshold" in decision.reasons
    assert "maker_ratio_below_observe_threshold" in decision.reasons
    assert "commission_bps_above_observe_threshold" in decision.reasons

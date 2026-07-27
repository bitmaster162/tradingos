from decimal import Decimal

from btcusdt_bot.monitoring.trade_reconciliation import (
    TradeReconciliationThresholds,
    evaluate_trade_reconciliation,
)


def test_trade_reconciliation_returns_trade_when_local_and_exchange_match() -> None:
    runtime_state = {
        "trade_fills": {
            "101": {
                "symbol": "BTCUSDT",
                "trade_id": 101,
                "order_id": 11,
                "trade_time_ms": 1_700_000_000_000,
                "quote_qty": "100.0",
                "realized_pnl": "1.25",
                "commission": "0.05",
            }
        }
    }
    exchange_trades = [{"id": 101, "orderId": 11, "quoteQty": "100.0", "realizedPnl": "1.25", "commission": "0.05"}]
    income_rows = [
        {"incomeType": "REALIZED_PNL", "income": "1.25", "tradeId": "101"},
        {"incomeType": "COMMISSION", "income": "-0.05", "tradeId": "101"},
    ]

    decision = evaluate_trade_reconciliation(
        runtime_state=runtime_state,
        symbol="BTCUSDT",
        exchange_user_trades=exchange_trades,
        exchange_income_rows=income_rows,
        lookback_start_ms=1_699_999_000_000,
        lookback_end_ms=1_700_000_010_000,
        compared_at_ms=1_700_000_010_000,
        window_mode="session",
        session_started_at_ms=1_699_999_500_000,
    )

    assert decision.action == "trade"
    assert decision.window_mode == "session"
    assert decision.session_started_at_ms == 1_699_999_500_000
    assert decision.matched_trade_count == 1
    assert decision.matched_order_count == 1
    assert decision.realized_pnl_diff_usdt == Decimal("0")
    assert decision.commission_abs_diff_usdt == Decimal("0")
    assert decision.quote_qty_abs_diff_usdt == Decimal("0")
    assert decision.income_trade_link_gap_ratio == Decimal("0")



def test_trade_reconciliation_observe_only_when_exchange_has_missing_local_fills() -> None:
    decision = evaluate_trade_reconciliation(
        runtime_state={"trade_fills": {}},
        symbol="BTCUSDT",
        exchange_user_trades=[{"id": 202, "orderId": 22, "quoteQty": "90.0", "realizedPnl": "5.0", "commission": "0.5"}],
        exchange_income_rows=[
            {"incomeType": "REALIZED_PNL", "income": "5.0", "tradeId": "202"},
            {"incomeType": "COMMISSION", "income": "-0.5", "tradeId": "202"},
        ],
        lookback_start_ms=1_699_999_000_000,
        lookback_end_ms=1_700_000_010_000,
        compared_at_ms=1_700_000_010_000,
        thresholds=TradeReconciliationThresholds(
            max_missing_local_trade_ratio_reduce=Decimal("0.10"),
            max_missing_local_trade_ratio_observe=Decimal("0.20"),
        ),
    )

    assert decision.action == "observe_only"
    assert decision.missing_local_trade_count == 1
    assert decision.missing_local_trade_ratio == Decimal("1")
    assert "missing_local_trade_ratio_above_observe_threshold" in decision.reasons



def test_trade_reconciliation_flags_missing_local_order_ratio() -> None:
    runtime_state = {
        "trade_fills": {
            "501": {
                "symbol": "BTCUSDT",
                "trade_id": 501,
                "order_id": 51,
                "trade_time_ms": 1_700_000_000_100,
                "quote_qty": "50.0",
                "realized_pnl": "0.5",
                "commission": "0.02",
            }
        }
    }
    exchange_trades = [
        {"id": 501, "orderId": 51, "quoteQty": "50.0", "realizedPnl": "0.5", "commission": "0.02"},
        {"id": 502, "orderId": 52, "quoteQty": "50.0", "realizedPnl": "0.5", "commission": "0.02"},
    ]
    income_rows = [
        {"incomeType": "REALIZED_PNL", "income": "1.0", "tradeId": "501"},
        {"incomeType": "COMMISSION", "income": "-0.04", "tradeId": "501"},
    ]

    decision = evaluate_trade_reconciliation(
        runtime_state=runtime_state,
        symbol="BTCUSDT",
        exchange_user_trades=exchange_trades,
        exchange_income_rows=income_rows,
        lookback_start_ms=1_699_999_000_000,
        lookback_end_ms=1_700_000_010_000,
        compared_at_ms=1_700_000_010_000,
        thresholds=TradeReconciliationThresholds(
            max_missing_local_order_ratio_reduce=Decimal("0.20"),
            max_missing_local_order_ratio_observe=Decimal("0.40"),
        ),
    )

    assert decision.action == "observe_only"
    assert decision.missing_local_order_count == 1
    assert decision.missing_local_order_ratio == Decimal("0.5")
    assert "missing_local_order_ratio_above_observe_threshold" in decision.reasons



def test_trade_reconciliation_flags_income_trade_link_gap_ratio() -> None:
    runtime_state = {
        "trade_fills": {
            "601": {
                "symbol": "BTCUSDT",
                "trade_id": 601,
                "order_id": 61,
                "trade_time_ms": 1_700_000_000_100,
                "quote_qty": "80.0",
                "realized_pnl": "2.0",
                "commission": "0.05",
            },
            "602": {
                "symbol": "BTCUSDT",
                "trade_id": 602,
                "order_id": 62,
                "trade_time_ms": 1_700_000_000_200,
                "quote_qty": "70.0",
                "realized_pnl": "1.0",
                "commission": "0.04",
            },
        }
    }
    exchange_trades = [
        {"id": 601, "orderId": 61, "quoteQty": "80.0", "realizedPnl": "2.0", "commission": "0.05"},
        {"id": 602, "orderId": 62, "quoteQty": "70.0", "realizedPnl": "1.0", "commission": "0.04"},
    ]
    income_rows = [
        {"incomeType": "COMMISSION", "income": "-0.05", "tradeId": "601"},
        {"incomeType": "REALIZED_PNL", "income": "2.0", "tradeId": "999999"},
    ]

    decision = evaluate_trade_reconciliation(
        runtime_state=runtime_state,
        symbol="BTCUSDT",
        exchange_user_trades=exchange_trades,
        exchange_income_rows=income_rows,
        lookback_start_ms=1_699_999_000_000,
        lookback_end_ms=1_700_000_010_000,
        compared_at_ms=1_700_000_010_000,
        thresholds=TradeReconciliationThresholds(
            max_income_trade_link_gap_ratio_reduce=Decimal("0.10"),
            max_income_trade_link_gap_ratio_observe=Decimal("0.40"),
            max_income_trade_realized_pnl_diff_usdt_reduce=Decimal("100.0"),
            max_income_trade_realized_pnl_diff_usdt_observe=Decimal("200.0"),
        ),
    )

    assert decision.action == "observe_only"
    assert decision.income_trade_linked_count == 1
    assert decision.income_trade_unlinked_count == 1
    assert decision.income_trade_link_gap_ratio == Decimal("0.5")
    assert "income_trade_link_gap_ratio_above_observe_threshold" in decision.reasons

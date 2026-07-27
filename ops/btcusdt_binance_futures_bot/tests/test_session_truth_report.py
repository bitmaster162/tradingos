from decimal import Decimal

from btcusdt_bot.reporting.session_truth_report import build_session_truth_report



def test_build_session_truth_report_computes_bucket_metrics_and_drawdown() -> None:
    start_ms = 1_700_000_000_000
    bucket_ms = 60_000
    user_trades = [
        {"id": 1, "orderId": 11, "quoteQty": "1000", "realizedPnl": "2.0", "commission": "0.5", "maker": True, "time": start_ms + 1_000},
        {"id": 2, "orderId": 12, "quoteQty": "1000", "realizedPnl": "-5.0", "commission": "0.5", "maker": False, "time": start_ms + bucket_ms + 1_000},
        {"id": 3, "orderId": 13, "quoteQty": "1000", "realizedPnl": "1.0", "commission": "0.5", "maker": True, "time": start_ms + (2 * bucket_ms) + 1_000},
    ]
    income_rows = [
        {"incomeType": "FUNDING_FEE", "income": "-0.2", "tranId": "f1", "time": start_ms + bucket_ms + 5_000},
    ]

    report = build_session_truth_report(
        exchange_user_trades=user_trades,
        exchange_income_rows=income_rows,
        lookback_start_ms=start_ms,
        lookback_end_ms=start_ms + (3 * bucket_ms) - 1,
        bucket_ms=bucket_ms,
        compared_at_ms=start_ms + (3 * bucket_ms),
        window_mode="session",
        session_started_at_ms=start_ms,
    )

    assert report.bucket_count == 3
    assert report.active_bucket_count == 3
    assert report.negative_bucket_count == 1
    assert report.negative_bucket_ratio == Decimal("0.3333333333333333333333333333")
    assert report.trailing_negative_bucket_streak == 0
    assert report.worst_bucket_net_realized_pnl_usdt == Decimal("-5.7")
    assert report.recent_bucket_net_realized_pnl_usdt == Decimal("0.5")
    assert report.recent_two_bucket_net_realized_pnl_usdt == Decimal("-5.2")
    assert report.cumulative_drawdown_usdt == Decimal("5.7")
    assert report.buckets[1].active is True
    assert report.buckets[1].exchange_funding_fee_usdt == Decimal("-0.2")

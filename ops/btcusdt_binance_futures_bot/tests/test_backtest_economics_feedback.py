import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.backtest.engine import BreakoutBacktestConfig, BreakoutBacktester, BacktestReport
from btcusdt_bot.backtest.execution_quality import build_execution_quality_report
from btcusdt_bot.backtest.reader import BacktestTick


def _ms(date_str: str, time_str: str) -> int:
    return int(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp() * 1000)


def _write_session_truth_report(root: Path, *, date: str, payload: dict[str, object]) -> None:
    report_dir = root / "reports" / date
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "btcusdt_session_truth_report.jsonl"
    path.write_text(json.dumps({"report": payload}) + "\n", encoding="utf-8")


def _sample_ticks(date: str) -> list[BacktestTick]:
    return [
        BacktestTick(event_time_ms=_ms(date, "00:00:00"), price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=_ms(date, "00:00:01"), price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=_ms(date, "00:00:02"), price=Decimal("101"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=_ms(date, "00:00:03"), price=Decimal("100.97"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=_ms(date, "00:00:05"), price=Decimal("101.20"), funding_rate=Decimal("0"), next_funding_time_ms=0),
    ]


def test_backtest_uses_prior_day_economics_feedback_for_sizing(tmp_path: Path) -> None:
    _write_session_truth_report(
        tmp_path,
        date="2026-04-06",
        payload={
            "compared_at_ms": _ms("2026-04-06", "23:59:59"),
            "active_bucket_count": 2,
            "negative_bucket_ratio": "1.0",
            "exchange_trade_count": 10,
            "exchange_order_count": 10,
            "exchange_quote_qty_usdt": "1000",
            "net_realized_pnl_usdt": "-10",
            "net_realized_bps": "-100",
            "maker_ratio": "0.10",
            "commission_bps": "8.0",
            "funding_bps": "-1.0",
            "recent_bucket_net_realized_bps": "-50",
            "recent_two_bucket_net_realized_bps": "-50",
            "cumulative_drawdown_usdt": "10",
        },
    )
    # Same-day report exists but should not be used because the backtest only sees completed prior days.
    _write_session_truth_report(
        tmp_path,
        date="2026-04-07",
        payload={
            "compared_at_ms": _ms("2026-04-07", "23:59:59"),
            "active_bucket_count": 2,
            "negative_bucket_ratio": "0.0",
            "exchange_trade_count": 10,
            "exchange_order_count": 10,
            "exchange_quote_qty_usdt": "1000",
            "net_realized_pnl_usdt": "50",
            "net_realized_bps": "50",
            "maker_ratio": "0.90",
            "commission_bps": "1.0",
            "funding_bps": "0.0",
            "recent_bucket_net_realized_bps": "50",
            "recent_two_bucket_net_realized_bps": "50",
            "cumulative_drawdown_usdt": "1",
        },
    )

    backtester = BreakoutBacktester(
        symbol="BTCUSDT",
        economics_data_dir=tmp_path,
        config=BreakoutBacktestConfig(
            breakout_lookback_ticks=2,
            atr_window_ticks=1,
            max_hold_seconds=1,
            position_notional_usdt=Decimal("100"),
            economics_lookback_days=1,
            economics_feedback_enabled=True,
            economics_feedback_min_active_day_count=1,
            economics_regime_enabled=False,
            min_notional_multiplier=Decimal("1.0"),
            max_notional_multiplier=Decimal("1.0"),
            sizing_flow_weight=Decimal("0"),
            sizing_crowding_weight=Decimal("0"),
            sizing_divergence_penalty_weight=Decimal("0"),
            sizing_funding_penalty_weight=Decimal("0"),
            volatility_target_atr_fraction=None,
        ),
    )

    report = backtester.run(_sample_ticks("2026-04-07"))

    assert report.trade_count == 1
    assert report.last_economics_dashboard_end_date == "2026-04-06"
    assert report.last_economics_feedback_reason == "sample_ready"
    assert report.average_economics_feedback_multiplier is not None
    assert report.average_economics_feedback_multiplier < Decimal("1")
    assert report.average_entry_notional < Decimal("100")


def test_backtest_economics_regime_observe_only_blocks_entry(tmp_path: Path) -> None:
    for date in ("2026-04-04", "2026-04-05", "2026-04-06"):
        _write_session_truth_report(
            tmp_path,
            date=date,
            payload={
                "compared_at_ms": _ms(date, "23:59:59"),
                "active_bucket_count": 3,
                "negative_bucket_ratio": "1.0",
                "exchange_trade_count": 12,
                "exchange_order_count": 12,
                "exchange_quote_qty_usdt": "1000",
                "net_realized_pnl_usdt": "-20",
                "net_realized_bps": "-200",
                "maker_ratio": "0.05",
                "commission_bps": "9.0",
                "funding_bps": "-1.5",
                "recent_bucket_net_realized_bps": "-75",
                "recent_two_bucket_net_realized_bps": "-60",
                "cumulative_drawdown_usdt": "30",
            },
        )

    backtester = BreakoutBacktester(
        symbol="BTCUSDT",
        economics_data_dir=tmp_path,
        config=BreakoutBacktestConfig(
            breakout_lookback_ticks=2,
            atr_window_ticks=1,
            max_hold_seconds=1,
            position_notional_usdt=Decimal("100"),
            economics_lookback_days=3,
            economics_feedback_enabled=True,
            economics_feedback_min_active_day_count=1,
            economics_regime_enabled=True,
            economics_regime_min_active_day_count=3,
        ),
    )

    report = backtester.run(_sample_ticks("2026-04-07"))

    assert report.trade_count == 0
    assert report.economics_regime_observe_rejections >= 1
    assert report.signal_gate_rejections == report.economics_regime_observe_rejections
    assert report.last_economics_regime_action == "observe_only"
    assert report.last_economics_feedback_reason == "economics_regime_non_trade"


def test_execution_quality_report_includes_backtest_economics_fields() -> None:
    report = BacktestReport(
        ticks=10,
        economics_feedback_decision_count=2,
        economics_feedback_multiplier_sum=Decimal("1.5"),
        economics_regime_reduce_size_applications=1,
        economics_regime_observe_rejections=2,
    )

    execution_quality = build_execution_quality_report(report)

    assert execution_quality.average_economics_feedback_multiplier == Decimal("0.75")
    assert execution_quality.economics_regime_reduce_size_applications == 1
    assert execution_quality.economics_regime_observe_rejections == 2

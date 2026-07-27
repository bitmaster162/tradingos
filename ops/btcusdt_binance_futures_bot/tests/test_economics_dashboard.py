import json
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.reporting.economics_dashboard import build_economics_dashboard



def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")



def test_build_economics_dashboard_aggregates_recent_days(tmp_path) -> None:
    for date, pnl, bps, maker_ratio, negative_bucket_ratio in [
        ("2026-04-05", "5.0", "0.50", "0.60", "0.25"),
        ("2026-04-06", "-8.0", "-0.80", "0.40", "0.50"),
        ("2026-04-07", "-4.0", "-0.40", "0.30", "0.75"),
    ]:
        _append_jsonl(
            tmp_path / "reports" / date / "btcusdt_session_truth_report.jsonl",
            {
                "report": {
                    "active_bucket_count": 4,
                    "exchange_trade_count": 10,
                    "exchange_order_count": 6,
                    "exchange_quote_qty_usdt": "10000",
                    "net_realized_pnl_usdt": pnl,
                    "net_realized_bps": bps,
                    "maker_ratio": maker_ratio,
                    "commission_bps": "3.0",
                    "funding_bps": "-0.10",
                    "negative_bucket_ratio": negative_bucket_ratio,
                    "recent_bucket_net_realized_bps": bps,
                    "recent_two_bucket_net_realized_bps": bps,
                    "cumulative_drawdown_usdt": "0",
                }
            },
        )

    dashboard = build_economics_dashboard(
        data_dir=tmp_path,
        symbol="BTCUSDT",
        end_date="2026-04-07",
        lookback_days=3,
    )

    assert dashboard.available_day_count == 3
    assert dashboard.active_day_count == 3
    assert dashboard.negative_day_count == 2
    assert dashboard.negative_day_ratio == Decimal("0.6666666666666666666666666667")
    assert dashboard.trailing_negative_day_streak == 2
    assert dashboard.total_exchange_trade_count == 30
    assert dashboard.total_net_realized_pnl_usdt == Decimal("-7.0")
    assert dashboard.aggregate_net_realized_bps == Decimal("-2.333333333333333333333333333")
    assert dashboard.recent_day_net_realized_bps == Decimal("-0.40")
    assert dashboard.recent_two_day_net_realized_bps == Decimal("-6")
    assert dashboard.average_maker_ratio == Decimal("0.4333333333333333333333333333")
    assert dashboard.cumulative_drawdown_usdt == Decimal("12.0")
    assert dashboard.best_day_date == "2026-04-05"
    assert dashboard.worst_day_date == "2026-04-06"

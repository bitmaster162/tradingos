import json
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.reporting.aggregate import aggregate_daily_reports


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def test_aggregate_daily_reports_summarizes_live_backtest_and_drift(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "2026-04-07"
    _append_jsonl(
        report_dir / "btcusdt_live_execution_quality.jsonl",
        {
            "report": {
                "average_expected_fill_ratio": "0.52",
                "average_exit_depth_sweep_bps": "2.1",
                "average_queue_clear_seconds": "1.7",
                "last_execution_drift_action": "reduce_size",
            }
        },
    )
    _append_jsonl(
        report_dir / "btcusdt_backtest_reports.jsonl",
        {
            "mode": "multistream_parity",
            "summary": {"net_pnl": "12.5", "trade_count": 8},
            "execution_quality": {"average_expected_fill_ratio": "0.60"},
        },
    )
    _append_jsonl(
        report_dir / "btcusdt_execution_drift.jsonl",
        {"decision": {"action": "reduce_size", "score": "2", "size_multiplier": "0.5"}},
    )

    aggregate = aggregate_daily_reports(data_dir=tmp_path, symbol="BTCUSDT", date="2026-04-07")

    assert aggregate.live_session_count == 1
    assert aggregate.backtest_run_count == 1
    assert aggregate.drift_check_count == 1
    assert aggregate.latest_live_action == "reduce_size"
    assert aggregate.average_live_expected_fill_ratio == Decimal("0.52")
    assert aggregate.average_backtest_net_pnl == Decimal("12.5")
    assert aggregate.latest_backtest_trade_count == 8
    assert aggregate.latest_drift_action == "reduce_size"
    assert aggregate.reduce_size_checks == 1



def test_aggregate_daily_reports_includes_trade_reconciliation_lineage_metrics(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "2026-04-07"
    _append_jsonl(
        report_dir / "btcusdt_trade_reconciliation.jsonl",
        {
            "decision": {
                "action": "reduce_size",
                "window_mode": "session",
                "income_trade_link_gap_ratio": "0.25",
                "quote_qty_abs_diff_usdt": "12.5",
            }
        },
    )

    aggregate = aggregate_daily_reports(data_dir=tmp_path, symbol="BTCUSDT", date="2026-04-07")

    assert aggregate.trade_reconciliation_check_count == 1
    assert aggregate.latest_trade_reconciliation_action == "reduce_size"
    assert aggregate.latest_trade_reconciliation_window_mode == "session"
    assert aggregate.latest_trade_reconciliation_income_trade_link_gap_ratio == Decimal("0.25")
    assert aggregate.latest_trade_reconciliation_quote_qty_abs_diff_usdt == Decimal("12.5")
    assert aggregate.average_trade_reconciliation_income_trade_link_gap_ratio == Decimal("0.25")


def test_aggregate_daily_reports_includes_authoritative_backfill_counts(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "2026-04-07"
    _append_jsonl(
        report_dir / "btcusdt_authoritative_backfill.jsonl",
        {"result": {"user_trade_row_count": 7, "income_row_count": 11}},
    )

    aggregate = aggregate_daily_reports(data_dir=tmp_path, symbol="BTCUSDT", date="2026-04-07")

    assert aggregate.authoritative_backfill_count == 1
    assert aggregate.latest_authoritative_backfill_trade_rows == 7
    assert aggregate.latest_authoritative_backfill_income_rows == 11

import json
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.reporting.aggregate import aggregate_daily_reports



def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")



def test_aggregate_daily_reports_includes_session_truth_report_and_trend_counts(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "2026-04-07"
    _append_jsonl(
        report_dir / "btcusdt_live_execution_quality.jsonl",
        {"report": {"last_session_truth_trend_action": "observe_only", "average_expected_fill_ratio": "0.5"}},
    )
    _append_jsonl(
        report_dir / "btcusdt_session_truth_report.jsonl",
        {"report": {"negative_bucket_ratio": "0.50", "cumulative_drawdown_usdt": "6.0"}},
    )
    _append_jsonl(
        report_dir / "btcusdt_session_truth_trend.jsonl",
        {"decision": {"action": "reduce_size", "negative_bucket_ratio": "0.50", "recent_bucket_net_realized_bps": "-1.5"}},
    )
    _append_jsonl(
        report_dir / "btcusdt_session_truth_trend.jsonl",
        {"decision": {"action": "observe_only", "negative_bucket_ratio": "0.75", "recent_bucket_net_realized_bps": "-4.0"}},
    )

    aggregate = aggregate_daily_reports(data_dir=tmp_path, symbol="BTCUSDT", date="2026-04-07")

    assert aggregate.session_truth_report_count == 1
    assert aggregate.session_truth_trend_check_count == 2
    assert aggregate.latest_session_truth_report_negative_bucket_ratio == Decimal("0.50")
    assert aggregate.average_session_truth_report_cumulative_drawdown_usdt == Decimal("6.0")
    assert aggregate.latest_session_truth_trend_action == "observe_only"
    assert aggregate.session_truth_trend_reduce_size_checks == 1
    assert aggregate.session_truth_trend_observe_only_checks == 1
    assert aggregate.latest_live_action in {"reduce_size", "observe_only"}

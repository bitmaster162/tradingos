import json
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.reporting.aggregate import aggregate_daily_reports


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def test_aggregate_daily_reports_includes_trade_reconciliation_counts(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "2026-04-07"
    _append_jsonl(
        report_dir / "btcusdt_live_execution_quality.jsonl",
        {"report": {"last_trade_reconciliation_action": "reduce_size", "average_expected_fill_ratio": "0.5"}},
    )
    _append_jsonl(
        report_dir / "btcusdt_trade_reconciliation.jsonl",
        {"decision": {"action": "reduce_size", "size_multiplier": "0.6"}},
    )
    _append_jsonl(
        report_dir / "btcusdt_trade_reconciliation.jsonl",
        {"decision": {"action": "observe_only", "size_multiplier": "0"}},
    )

    aggregate = aggregate_daily_reports(data_dir=tmp_path, symbol="BTCUSDT", date="2026-04-07")

    assert aggregate.trade_reconciliation_check_count == 2
    assert aggregate.latest_trade_reconciliation_action == "observe_only"
    assert aggregate.trade_reconciliation_reduce_size_checks == 1
    assert aggregate.trade_reconciliation_observe_only_checks == 1
    assert aggregate.latest_live_action == "reduce_size" or aggregate.latest_live_action == "observe_only"
    assert aggregate.average_live_expected_fill_ratio == Decimal("0.5")

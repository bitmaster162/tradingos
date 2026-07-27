import json
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.reporting.aggregate import aggregate_daily_reports


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def test_aggregate_daily_reports_includes_combined_protection_layer(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "2026-04-07"
    _append_jsonl(
        report_dir / "btcusdt_live_execution_quality.jsonl",
        {
            "report": {
                "average_expected_fill_ratio": "0.55",
                "last_execution_drift_action": "trade",
                "last_intraday_protection_action": "trade",
                "last_pnl_protection_action": "reduce_size",
                "last_combined_protection_action": "observe_only",
            }
        },
    )
    _append_jsonl(
        report_dir / "btcusdt_combined_protection.jsonl",
        {"decision": {"action": "observe_only", "score": "4", "size_multiplier": "0"}},
    )

    aggregate = aggregate_daily_reports(data_dir=tmp_path, symbol="BTCUSDT", date="2026-04-07")

    assert aggregate.combined_protection_check_count == 1
    assert aggregate.latest_combined_protection_action == "observe_only"
    assert aggregate.combined_observe_only_checks == 1
    assert aggregate.latest_live_action == "observe_only"
    assert aggregate.average_live_expected_fill_ratio == Decimal("0.55")

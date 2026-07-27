import json
from pathlib import Path

from btcusdt_bot.reporting.aggregate import aggregate_daily_reports


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def test_aggregate_daily_reports_includes_intraday_protection_counts(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "2026-04-07"
    _append_jsonl(
        report_dir / "btcusdt_intraday_protection.jsonl",
        {"decision": {"action": "reduce_size", "size_multiplier": "0.6"}},
    )
    _append_jsonl(
        report_dir / "btcusdt_intraday_protection.jsonl",
        {"decision": {"action": "observe_only", "size_multiplier": "0"}},
    )

    aggregate = aggregate_daily_reports(data_dir=tmp_path, symbol="BTCUSDT", date="2026-04-07")

    assert aggregate.intraday_protection_check_count == 2
    assert aggregate.latest_intraday_protection_action == "observe_only"
    assert aggregate.intraday_reduce_size_checks == 1
    assert aggregate.intraday_observe_only_checks == 1

import json
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.reporting.aggregate import aggregate_daily_reports


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def test_aggregate_daily_reports_includes_walkforward_metrics(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports" / "2026-04-07"
    _append_jsonl(
        report_dir / "btcusdt_walkforward_reports.jsonl",
        {
            "summary": {
                "fold_count": 3,
                "total_test_net_pnl": "15.5",
                "selection_turnover_ratio": "0.5",
            }
        },
    )

    aggregate = aggregate_daily_reports(data_dir=tmp_path, symbol="BTCUSDT", date="2026-04-07")

    assert aggregate.walkforward_report_count == 1
    assert aggregate.latest_walkforward_fold_count == 3
    assert aggregate.latest_walkforward_total_test_net_pnl == Decimal("15.5")
    assert aggregate.latest_walkforward_selection_turnover_ratio == Decimal("0.5")

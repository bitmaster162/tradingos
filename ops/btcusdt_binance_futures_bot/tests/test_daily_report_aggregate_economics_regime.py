import json
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.reporting.aggregate import aggregate_daily_reports



def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")



def test_aggregate_daily_reports_includes_economics_dashboard_and_regime(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "2026-04-07"
    _append_jsonl(
        report_dir / "btcusdt_economics_dashboard.jsonl",
        {"dashboard": {"negative_day_ratio": "0.50", "average_maker_ratio": "0.45"}},
    )
    _append_jsonl(
        report_dir / "btcusdt_economics_regime.jsonl",
        {"decision": {"action": "reduce_size", "negative_day_ratio": "0.50", "recent_day_net_realized_bps": "-1.5"}},
    )
    _append_jsonl(
        report_dir / "btcusdt_economics_regime.jsonl",
        {"decision": {"action": "observe_only", "negative_day_ratio": "0.75", "recent_day_net_realized_bps": "-4.0"}},
    )

    aggregate = aggregate_daily_reports(data_dir=tmp_path, symbol="BTCUSDT", date="2026-04-07")

    assert aggregate.economics_dashboard_count == 1
    assert aggregate.economics_regime_check_count == 2
    assert aggregate.latest_economics_dashboard_negative_day_ratio == Decimal("0.50")
    assert aggregate.average_economics_dashboard_average_maker_ratio == Decimal("0.45")
    assert aggregate.latest_economics_regime_action == "observe_only"
    assert aggregate.economics_regime_reduce_size_checks == 1
    assert aggregate.economics_regime_observe_only_checks == 1
    assert aggregate.latest_economics_regime_recent_day_net_realized_bps == Decimal("-4.0")

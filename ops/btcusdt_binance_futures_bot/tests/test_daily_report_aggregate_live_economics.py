import json
from decimal import Decimal

from btcusdt_bot.reporting.aggregate import aggregate_daily_reports



def test_daily_report_aggregate_includes_live_economics_feedback(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "2026-04-07"
    report_dir.mkdir(parents=True)
    (report_dir / "btcusdt_live_execution_quality.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"report": {"average_economics_feedback_multiplier": "0.80", "last_economics_regime_action": "trade"}}),
                json.dumps({"report": {"average_economics_feedback_multiplier": "0.60", "last_economics_regime_action": "reduce_size"}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    aggregate = aggregate_daily_reports(data_dir=tmp_path, symbol="BTCUSDT", date="2026-04-07")

    assert aggregate.latest_live_action == "reduce_size"
    assert aggregate.average_live_economics_feedback_multiplier == Decimal("0.70")
    assert aggregate.latest_live_economics_feedback_multiplier == Decimal("0.60")

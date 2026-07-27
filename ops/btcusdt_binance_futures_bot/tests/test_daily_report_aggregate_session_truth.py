import json
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.reporting.aggregate import aggregate_daily_reports



def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")



def test_aggregate_daily_reports_includes_session_truth_counts(tmp_path) -> None:
    report_dir = tmp_path / "reports" / "2026-04-07"
    _append_jsonl(
        report_dir / "btcusdt_live_execution_quality.jsonl",
        {"report": {"last_session_truth_action": "reduce_size", "average_expected_fill_ratio": "0.5"}},
    )
    _append_jsonl(
        report_dir / "btcusdt_session_truth.jsonl",
        {"decision": {"action": "reduce_size", "net_realized_pnl_usdt": "-1.5", "net_realized_bps": "-2.0", "maker_ratio": "0.40"}},
    )
    _append_jsonl(
        report_dir / "btcusdt_session_truth.jsonl",
        {"decision": {"action": "observe_only", "net_realized_pnl_usdt": "-3.0", "net_realized_bps": "-4.5", "maker_ratio": "0.10"}},
    )

    aggregate = aggregate_daily_reports(data_dir=tmp_path, symbol="BTCUSDT", date="2026-04-07")

    assert aggregate.session_truth_check_count == 2
    assert aggregate.latest_session_truth_action == "observe_only"
    assert aggregate.session_truth_reduce_size_checks == 1
    assert aggregate.session_truth_observe_only_checks == 1
    assert aggregate.latest_session_truth_net_realized_bps == Decimal("-4.5")
    assert aggregate.average_session_truth_maker_ratio == Decimal("0.25")
    assert aggregate.latest_live_action in {"reduce_size", "observe_only"}

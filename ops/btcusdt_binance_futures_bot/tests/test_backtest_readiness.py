from __future__ import annotations

import json
from pathlib import Path

from btcusdt_bot.backtest.readiness import build_backtest_readiness_report


def _write_jsonl(path: Path, rows: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for index in range(rows):
            handle.write(json.dumps({"row": index}) + "\n")


def test_backtest_readiness_recommends_mark_only_when_parity_streams_missing(tmp_path: Path) -> None:
    for day in ("2026-04-01", "2026-04-02"):
        _write_jsonl(tmp_path / "market" / day / "btcusdt_markPrice_1s.jsonl", rows=6)

    report = build_backtest_readiness_report(
        tmp_path,
        symbol="BTCUSDT",
        start_date="2026-04-01",
        end_date="2026-04-02",
        mark_only=False,
        crowding_period="5m",
        depth_levels=20,
        use_rpi_depth_fills=True,
        ignore_contract_status=False,
    )

    assert report.recommendation == "mark_only_only"
    assert set(report.missing_required_streams) >= {"agg_trade", "book_ticker", "local_depth_20", "crowding_5m", "contract_info"}
    assert report.recommended_command.endswith("--mark-only")


def test_backtest_readiness_detects_multistream_ready(tmp_path: Path) -> None:
    days = ("2026-04-01", "2026-04-02")
    for day in days:
        _write_jsonl(tmp_path / "market" / day / "btcusdt_markPrice_1s.jsonl", rows=120)
        _write_jsonl(tmp_path / "market" / day / "btcusdt_aggTrade.jsonl", rows=120)
        _write_jsonl(tmp_path / "market" / day / "contractInfo.jsonl", rows=120)
        _write_jsonl(tmp_path / "public" / day / "btcusdt@bookTicker.jsonl", rows=120)
        _write_jsonl(tmp_path / "public" / day / "btcusdt_localDepth20.jsonl", rows=120)
        _write_jsonl(tmp_path / "public" / day / "btcusdt_localRpiDepth20.jsonl", rows=120)
        _write_jsonl(tmp_path / "crowding" / day / "btcusdt_5m.jsonl", rows=120)

    report = build_backtest_readiness_report(
        tmp_path,
        symbol="BTCUSDT",
        start_date="2026-04-01",
        end_date="2026-04-02",
        mark_only=False,
        crowding_period="5m",
        depth_levels=20,
        use_rpi_depth_fills=True,
        ignore_contract_status=False,
    )

    assert report.recommendation == "multistream_ready"
    assert report.missing_required_streams == ()
    assert report.recommended_command.endswith("2026-04-02")

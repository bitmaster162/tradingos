import json
from pathlib import Path

from btcusdt_bot.backtest.reader import iter_market_events


def test_iter_market_events_reads_local_depth_snapshot(tmp_path: Path) -> None:
    day_dir = tmp_path / "public" / "2026-04-07"
    day_dir.mkdir(parents=True)
    payload = {
        "received_at_ms": 1,
        "stream": "btcusdt@depth@100ms",
        "event_type": "localDepthSnapshot",
        "payload": {
            "e": "localDepthSnapshot",
            "E": 1000,
            "T": 1000,
            "s": "BTCUSDT",
            "u": 101,
            "levels": 20,
            "imbalance": "0.2",
            "bids": [["100", "1"]],
            "asks": [["100.5", "1"]],
        },
    }
    (day_dir / "btcusdt_localDepth20.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")

    events = list(
        iter_market_events(
            tmp_path,
            symbol="BTCUSDT",
            include_agg_trades=False,
            include_book_ticker=False,
            include_local_depth=True,
            local_depth_levels=20,
        )
    )

    assert len(events) == 1
    assert events[0].event_type == "localDepthSnapshot"
    assert events[0].payload["levels"] == 20

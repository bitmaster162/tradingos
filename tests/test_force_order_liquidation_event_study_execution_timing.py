from __future__ import annotations

from types import SimpleNamespace

from tools.force_order_liquidation_event_study import build_event_records, write_event_records


def bar(ts: str, open_price: float, close_price: float):
    return SimpleNamespace(ts=ts, open=open_price, close=close_price)


def test_event_study_enters_next_bar_open_not_event_bar_close() -> None:
    rows = [
        {
            "symbol": "BTCUSDT",
            "bar_ts": "2026-07-12T04:00:00.000Z",
            "dominant_context": "short_liquidation_squeeze",
            "total_notional_usd": 100000.0,
        }
    ]
    bars = {
        "BTCUSDT": [
            bar("2026-07-12T04:00:00Z", 150.0, 200.0),
            bar("2026-07-12T05:00:00Z", 100.0, 110.0),
            bar("2026-07-12T06:00:00Z", 110.0, 120.0),
        ]
    }

    records, errors = build_event_records(rows, bars, [1, 2])

    assert errors == []
    assert records[0]["entry_model"] == "next_bar_open"
    assert records[0]["independent_4h_block"] == "2026-07-12T04:00:00Z"
    assert records[0]["entry_price"] == 100.0
    assert records[0]["event_bar_close"] == 200.0
    assert records[0]["raw_return_bps"] == 1000.0
    assert records[1]["raw_return_bps"] == 2000.0


def test_event_study_waits_when_next_bar_open_is_unavailable() -> None:
    rows = [
        {
            "symbol": "BTCUSDT",
            "bar_ts": "2026-07-12T04:00:00.000Z",
            "dominant_context": "long_liquidation_flush",
            "total_notional_usd": 100000.0,
        }
    ]
    bars = {"BTCUSDT": [bar("2026-07-12T04:00:00Z", 100.0, 90.0)]}

    records, errors = build_event_records(rows, bars, [1])

    assert records == []
    assert errors == []


def test_event_records_are_written_in_deterministic_order(tmp_path) -> None:
    records = [
        {
            "symbol": "ETHUSDT",
            "bar_ts": "2026-07-12T04:00:00.000Z",
            "independent_4h_block": "2026-07-12T04:00:00Z",
            "signal_time": "event_bar_close",
            "entry_time": "2026-07-12T05:00:00.000Z",
            "entry_model": "next_bar_open",
            "entry_price": 100.0,
            "event_bar_close": 99.0,
            "exit_time": "2026-07-12T05:00:00.000Z",
            "exit_price": 101.0,
            "horizon_bars": 1,
            "dominant_context": "long_liquidation_flush",
            "total_notional_usd": 1000.0,
            "raw_return_bps": 100.0,
            "continuation_return_bps": -100.0,
            "reversal_return_bps": 100.0,
        },
        {
            "symbol": "BTCUSDT",
            "bar_ts": "2026-07-12T04:00:00.000Z",
            "independent_4h_block": "2026-07-12T04:00:00Z",
            "signal_time": "event_bar_close",
            "entry_time": "2026-07-12T05:00:00.000Z",
            "entry_model": "next_bar_open",
            "entry_price": 100.0,
            "event_bar_close": 99.0,
            "exit_time": "2026-07-12T05:00:00.000Z",
            "exit_price": 101.0,
            "horizon_bars": 1,
            "dominant_context": "long_liquidation_flush",
            "total_notional_usd": 1000.0,
            "raw_return_bps": 100.0,
            "continuation_return_bps": -100.0,
            "reversal_return_bps": 100.0,
        },
    ]
    path = tmp_path / "records.csv"

    write_event_records(path, records)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[1].startswith("BTCUSDT,")
    assert lines[2].startswith("ETHUSDT,")

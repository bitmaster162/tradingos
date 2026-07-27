from __future__ import annotations

import csv
from argparse import Namespace
from pathlib import Path

from tools.derivatives_event_forward_observer import run_once


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path: Path) -> Namespace:
    klines = []
    oi_rows = []
    for index in range(260):
        close = 100.0 + index
        if index == 259:
            close += 10.0
        klines.append(
            {
                "time": f"2026-01-{(index // 24) + 1:02d}T{index % 24:02d}:00:00+00:00",
                "open": close - 0.4,
                "high": close + 0.1,
                "low": close - 0.9,
                "close": close,
                "volume": 1000 + index,
            }
        )
        oi = 10000.0 + index
        if index == 259:
            oi += 500.0
        oi_rows.append(
            {
                "time": klines[-1]["time"],
                "open_interest": oi,
                "funding": 0.0001,
            }
        )
    klines_path = tmp_path / "4h_klines.csv"
    oi_path = tmp_path / "4h_oi_aligned.csv"
    _write_csv(klines_path, klines)
    _write_csv(oi_path, oi_rows)
    report = {
        "decision": "oos_pass_observer_candidate_not_trade_permission",
        "data": [
            {
                "interval": "4h",
                "klines_path": str(klines_path),
                "derivatives_path": str(oi_path),
            }
        ],
        "selected": {
            "config": {
                "strategy_id": "test_deriv_observer",
                "family": "oi_build_continuation",
                "side": "LONG",
                "interval": "4h",
                "lookback": 6,
                "price_atr": 0.8,
                "oi_pct": 0.25,
                "funding_abs": 0.0002,
                "volume_z": 0.0,
                "close_location": 0.55,
                "regime_filter": "ema50_stack",
                "stop_atr": 1.0,
                "take_atr": 3.0,
                "max_hold_bars": 8,
            }
        },
    }
    report_path = tmp_path / "miner.json"
    report_path.write_text(__import__("json").dumps(report), encoding="utf-8")
    return Namespace(
        miner_report=str(report_path),
        symbol="BTCUSDT",
        journal_path=str(tmp_path / "journal.jsonl"),
        state_path=str(tmp_path / "state.json"),
    )


def test_derivatives_event_forward_observer_writes_signal_and_dedupes(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    first = run_once(args)
    assert first["decision"] == "observer_signal_written"
    assert first["latest_observation"]["events_written"] == 1
    assert len(Path(args.journal_path).read_text(encoding="utf-8").splitlines()) == 1

    second = run_once(args)
    assert second["decision"] == "observer_signal_duplicate_suppressed"
    assert second["latest_observation"]["events_written"] == 0
    assert len(Path(args.journal_path).read_text(encoding="utf-8").splitlines()) == 1


def test_derivatives_event_forward_observer_blocks_missing_selection(tmp_path: Path) -> None:
    report_path = tmp_path / "miner.json"
    report_path.write_text('{"decision":"reject_validation_gate_failed"}', encoding="utf-8")
    args = Namespace(
        miner_report=str(report_path),
        symbol="BTCUSDT",
        journal_path=str(tmp_path / "journal.jsonl"),
        state_path=str(tmp_path / "state.json"),
    )
    report = run_once(args)
    assert report["decision"] == "blocked_no_selected_derivatives_candidate"
    assert report["can_trade"] is False

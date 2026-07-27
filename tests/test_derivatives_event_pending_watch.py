from __future__ import annotations

import csv
import json
from argparse import Namespace
from pathlib import Path

from tools.derivatives_event_pending_watch import build_report


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path: Path, *, impulse: bool) -> Path:
    klines = []
    oi_rows = []
    for index in range(260):
        close = 100.0 + index
        if impulse and index == 259:
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
        if impulse and index == 259:
            oi += 500.0
        oi_rows.append({"time": klines[-1]["time"], "open_interest": oi, "funding": 0.0001})
    klines_path = tmp_path / "4h_klines.csv"
    oi_path = tmp_path / "4h_oi_aligned.csv"
    _write_csv(klines_path, klines)
    _write_csv(oi_path, oi_rows)
    miner = {
        "data": [{"interval": "4h", "klines_path": str(klines_path), "derivatives_path": str(oi_path)}],
        "selected": {
            "config": {
                "strategy_id": "test_deriv_pending",
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
    miner_path = tmp_path / "miner.json"
    miner_path.write_text(json.dumps(miner), encoding="utf-8")
    return miner_path


def test_derivatives_event_pending_watch_signal_conditions_met(tmp_path: Path) -> None:
    miner_path = _fixture(tmp_path, impulse=True)
    report = build_report(Namespace(miner_report=str(miner_path)))
    assert report["decision"] == "pending_watch_signal_conditions_met"
    assert report["latest"]["summary"]["all_passed"] is True
    assert report["can_trade"] is False


def test_derivatives_event_pending_watch_reports_blockers(tmp_path: Path) -> None:
    miner_path = _fixture(tmp_path, impulse=False)
    report = build_report(Namespace(miner_report=str(miner_path)))
    assert report["decision"] in {"pending_watch_blocked", "pending_watch_near_signal"}
    assert report["latest"]["summary"]["all_passed"] is False
    assert report["latest"]["summary"]["blockers"]

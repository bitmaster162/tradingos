from __future__ import annotations

import csv
import json
from argparse import Namespace
from pathlib import Path

from tools.derivatives_event_forward_scoreboard import build_report as build_scoreboard
from tools.derivatives_event_promotion_gate import build_report as build_gate


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path: Path) -> dict[str, Path]:
    klines_path = tmp_path / "4h_klines.csv"
    _write_csv(
        klines_path,
        [
            {"time": "2026-01-01T00:00:00+00:00", "open": 99, "high": 101, "low": 98, "close": 100, "volume": 1000},
            {"time": "2026-01-01T04:00:00+00:00", "open": 100, "high": 103.5, "low": 99.8, "close": 103, "volume": 1000},
            {"time": "2026-01-01T08:00:00+00:00", "open": 103, "high": 104, "low": 102, "close": 103.5, "volume": 1000},
        ],
    )
    miner_path = tmp_path / "miner.json"
    miner = {
        "decision": "oos_pass_observer_candidate_not_trade_permission",
        "data": [{"interval": "4h", "klines_path": str(klines_path), "derivatives_path": str(tmp_path / "oi.csv")}],
        "selected": {
            "strategy_id": "test_deriv",
            "config": {
                "strategy_id": "test_deriv",
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
                "max_hold_bars": 2,
            },
            "oos": {"summary": {"trades": 13, "expectancy_r": 0.15}},
        },
    }
    miner_path.write_text(json.dumps(miner), encoding="utf-8")
    journal_path = tmp_path / "journal.jsonl"
    event = {
        "event_type": "derivatives_event_observer_signal",
        "strategy_id": "test_deriv",
        "side": "LONG",
        "interval": "4h",
        "bar_ts": "2026-01-01T00:00:00+00:00",
        "atr": 1.0,
        "stop_atr": 1.0,
        "take_atr": 3.0,
        "max_hold_bars": 2,
        "can_trade": False,
    }
    journal_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    observer_path = tmp_path / "observer.json"
    observer_path.write_text('{"decision":"observer_signal_written","can_trade":false}', encoding="utf-8")
    return {"miner": miner_path, "journal": journal_path, "observer": observer_path}


def test_derivatives_event_scoreboard_resolves_signal(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    report = build_scoreboard(
        Namespace(
            miner_report=str(paths["miner"]),
            journal_path=str(paths["journal"]),
            cost_bps_per_side=0.0,
            min_resolved=1,
            min_expectancy_r=0.03,
            max_drawdown_r=12.0,
            max_outcomes=200,
        )
    )
    assert report["summary"]["resolved"] == 1
    assert report["summary"]["wins"] == 1
    assert report["summary"]["expectancy_r"] == 3.0
    assert report["decision"] == "candidate_for_promotion_review"
    assert report["can_trade"] is False


def test_derivatives_event_promotion_gate_requires_forward_sample(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    scoreboard_path = tmp_path / "scoreboard.json"
    scoreboard_path.write_text(
        json.dumps({"summary": {"observer_signal_events": 1, "resolved": 1, "expectancy_r": 3.0, "max_drawdown_r": 0.0}}),
        encoding="utf-8",
    )
    blocked = build_gate(
        Namespace(
            miner_report=str(paths["miner"]),
            observer=str(paths["observer"]),
            scoreboard=str(scoreboard_path),
            min_oos_trades=10,
            min_forward_signals=30,
            min_resolved=30,
            min_expectancy_r=0.03,
            max_drawdown_r=12.0,
        )
    )
    assert blocked["decision"] == "blocked_waiting_derivatives_event_forward_evidence"
    assert blocked["promotion"]["paper_execution_allowed"] is False
    assert blocked["can_trade"] is False

    passed = build_gate(
        Namespace(
            miner_report=str(paths["miner"]),
            observer=str(paths["observer"]),
            scoreboard=str(scoreboard_path),
            min_oos_trades=10,
            min_forward_signals=1,
            min_resolved=1,
            min_expectancy_r=0.03,
            max_drawdown_r=12.0,
        )
    )
    assert passed["decision"] == "candidate_for_manual_paper_design_review_only"
    assert passed["promotion"]["paper_design_review_allowed"] is True
    assert passed["promotion"]["paper_execution_allowed"] is False

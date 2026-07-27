from __future__ import annotations

import json
from pathlib import Path

from tools.derivatives_event_runtime_drift_audit import build_report


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict) -> None:
    write(path, json.dumps(payload))


def seed_minimal(root: Path, *, decision: str = "reject", rows: int = 2) -> None:
    csv_text = "time,open,high,low,close,volume\n"
    for index in range(rows):
        csv_text += f"2026-01-01T0{index}:00:00+00:00,1,2,0,1,10\n"
    oi_text = "time,price,open_interest,volume,funding\n"
    for index in range(rows):
        oi_text += f"2026-01-01T0{index}:00:00+00:00,1,100,10,0.0001\n"
    for interval in ("1h", "4h"):
        write(root / f"data/cache/binance_spot_perp_extended/futures/BTCUSDT/{interval}_klines.csv", csv_text)
        write(root / f"data/cache/binance_spot_perp_extended/futures/BTCUSDT/{interval}_oi_aligned.csv", oi_text)
    write_json(
        root / "docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json",
        {"decision": decision, "selected": {"strategy_id": "candidate"}, "can_trade": False},
    )
    write_json(
        root / "docs/TRADINGOS_CORE_READINESS_EDGE_REPORT_2026-06-26.json",
        {
            "decision": "data_readiness_first_not_telegram",
            "scoreboard": {"derivatives_event_train_qualified": 1, "derivatives_event_validation_qualified": 0},
            "can_trade": False,
        },
    )


def test_drift_audit_reports_sync(tmp_path: Path) -> None:
    source = tmp_path / "source"
    runtime = tmp_path / "runtime"
    seed_minimal(source)
    seed_minimal(runtime)

    report = build_report(source, runtime)

    assert report["decision"] == "source_runtime_in_sync"
    assert report["data_drift_count"] == 0
    assert report["report_drift_count"] == 0
    assert report["can_trade"] is False


def test_drift_audit_blocks_on_data_and_report_drift(tmp_path: Path) -> None:
    source = tmp_path / "source"
    runtime = tmp_path / "runtime"
    seed_minimal(source, decision="reject", rows=2)
    seed_minimal(runtime, decision="oos_pass", rows=3)

    report = build_report(source, runtime)

    assert report["decision"] == "source_runtime_data_drift_detected_do_not_promote"
    assert report["data_drift_count"] > 0
    assert report["report_drift_count"] > 0
    assert report["next_action"] == "reconcile source/runtime data caches before accepting derivatives-event candidate"


def test_drift_audit_ignores_report_hash_when_semantics_match(tmp_path: Path) -> None:
    source = tmp_path / "source"
    runtime = tmp_path / "runtime"
    seed_minimal(source, decision="same", rows=2)
    seed_minimal(runtime, decision="same", rows=2)
    write_json(
        source / "docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json",
        {"generated_at": "a", "decision": "same", "selected": {"strategy_id": "candidate"}, "can_trade": False},
    )
    write_json(
        runtime / "docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json",
        {"generated_at": "b", "decision": "same", "selected": {"strategy_id": "candidate"}, "can_trade": False},
    )

    report = build_report(source, runtime)

    assert report["report_drift_count"] == 0

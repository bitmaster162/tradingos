import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tools import liquidation_force_order_data_quality as module


build_report = module.build_report


class Args:
    data_dir = ""
    collector_status = ""
    collector_heartbeat = ""
    latest_collector_report = ""
    contract = ""
    prereg_lock = ""
    research_symbols = "BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT"
    min_events_for_research = 2
    max_status_age_minutes = 100_000
    max_heartbeat_age_minutes = 100_000
    max_bad_lines = 10


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_prereg(path: Path, event_start_at: str = "2026-06-30T00:00:00Z") -> None:
    write_json(
        path,
        {
            "lock_id": "test_force_order_lock",
            "status": "accepted_preregistered_research_only",
            "can_trade": False,
            "fixed_study": {
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BCHUSDT"],
                "interval": "1h",
                "signal_time": "event_bar_close",
                "entry_time": "next_bar_open",
                "return_measurement": "next_bar_open_to_horizon_close",
                "event_start_at": event_start_at,
                "minimum_events": 2,
            },
        },
    )


def test_empty_feed_is_not_ready_not_hard_fail():
    tmp = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp.name)
    args = Args()
    args.data_dir = str(tmp_path / "data")
    args.collector_status = str(tmp_path / "status.json")
    args.latest_collector_report = str(tmp_path / "latest.json")
    args.collector_heartbeat = str(tmp_path / "heartbeat.json")
    args.contract = str(tmp_path / "contract.json")
    args.prereg_lock = str(tmp_path / "prereg.json")
    write_json(Path(args.collector_status), {"ts": "2026-06-30T00:00:00Z", "status": "running_collector_cycle", "pid": os.getpid()})
    write_json(Path(args.latest_collector_report), {"decision": "connected_no_events", "stats": {"parse_errors": [], "liveness_messages_seen": 1}})
    write_json(Path(args.collector_heartbeat), {"ts": "2026-06-30T00:00:00Z", "can_trade": False, "liveness_messages_seen": 1})
    write_json(Path(args.contract), {"can_trade": False})
    write_prereg(Path(args.prereg_lock))

    report = build_report(args)

    assert report["decision"] == "liquidation_force_order_collector_alive_no_events_yet"
    assert report["events"]["events"] == 0
    assert report["can_trade"] is False
    assert report["strategy_consumer_allowed"] is False
    tmp.cleanup()


def test_synthetic_sample_row_is_rejected():
    tmp = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp.name)
    args = Args()
    args.data_dir = str(tmp_path / "data")
    args.collector_status = str(tmp_path / "status.json")
    args.latest_collector_report = str(tmp_path / "latest.json")
    args.collector_heartbeat = str(tmp_path / "heartbeat.json")
    args.contract = str(tmp_path / "contract.json")
    args.prereg_lock = str(tmp_path / "prereg.json")
    write_json(Path(args.collector_status), {"ts": "2026-06-30T00:00:00Z", "status": "running_collector_cycle", "pid": os.getpid()})
    write_json(Path(args.latest_collector_report), {"decision": "connected_no_events", "stats": {"parse_errors": [], "liveness_messages_seen": 1}})
    write_json(Path(args.collector_heartbeat), {"ts": "2026-06-30T00:00:00Z", "can_trade": False, "liveness_messages_seen": 1})
    write_json(Path(args.contract), {"can_trade": False})
    write_prereg(Path(args.prereg_lock))
    row = {
        "event_time_ms": 1760000000000,
        "event_time": "2025-10-09T08:53:20.000Z",
        "trade_time_ms": 1760000000000,
        "trade_time": "2025-10-09T08:53:20.000Z",
        "symbol": "BTCUSDT",
        "side": "SELL",
        "price": 99950.0,
        "quantity": 0.01,
        "notional_usd": 999.5,
        "source": "binance_usdm_forceOrder_websocket",
        "is_real_liquidation_feed": True,
        "raw": {"E": 1760000000000},
    }
    path = Path(args.data_dir) / "BTCUSDT" / "20251009.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    report = build_report(args)

    assert report["validation"]["synthetic_rows"] == 1
    assert any(item["name"] == "synthetic_rows_zero" and not item["passed"] for item in report["gates"])
    tmp.cleanup()


def test_silent_transport_is_a_hard_failure():
    tmp = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp.name)
    args = Args()
    args.data_dir = str(tmp_path / "data")
    args.collector_status = str(tmp_path / "status.json")
    args.collector_heartbeat = str(tmp_path / "heartbeat.json")
    args.latest_collector_report = str(tmp_path / "latest.json")
    args.contract = str(tmp_path / "contract.json")
    args.prereg_lock = str(tmp_path / "prereg.json")
    write_json(Path(args.collector_status), {"ts": "2026-06-30T00:00:00Z", "status": "running_collector_cycle", "pid": os.getpid()})
    write_json(Path(args.collector_heartbeat), {"ts": "2026-06-30T00:00:00Z", "can_trade": False, "liveness_messages_seen": 0})
    write_json(Path(args.latest_collector_report), {"decision": "silent", "stats": {"parse_errors": [], "liveness_messages_seen": 0, "events_written": 0}})
    write_json(Path(args.contract), {"can_trade": False})
    write_prereg(Path(args.prereg_lock))

    report = build_report(args)

    assert report["decision"] == "liquidation_force_order_data_quality_hard_fail"
    assert any(item["name"] == "collector_transport_liveness" and not item["passed"] for item in report["hard_failures"])
    assert report["strategy_consumer_allowed"] is False
    tmp.cleanup()


def test_readiness_counts_only_fixed_research_universe():
    tmp = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp.name)
    args = Args()
    args.data_dir = str(tmp_path / "data")
    args.collector_status = str(tmp_path / "status.json")
    args.collector_heartbeat = str(tmp_path / "heartbeat.json")
    args.latest_collector_report = str(tmp_path / "latest.json")
    args.contract = str(tmp_path / "contract.json")
    args.prereg_lock = str(tmp_path / "prereg.json")
    args.min_events_for_research = 1
    write_json(Path(args.collector_status), {"ts": "2026-06-30T00:00:00Z", "status": "running_collector_cycle", "pid": os.getpid()})
    write_json(Path(args.collector_heartbeat), {"ts": "2026-06-30T00:00:00Z", "can_trade": False, "liveness_messages_seen": 1})
    write_json(Path(args.latest_collector_report), {"decision": "live", "stats": {"parse_errors": [], "liveness_messages_seen": 1}})
    write_json(Path(args.contract), {"can_trade": False})
    write_prereg(Path(args.prereg_lock), "2026-07-12T03:00:00Z")
    base = {
        "event_time_ms": 1783827000000,
        "event_time": "2026-07-12T03:30:00.000Z",
        "trade_time_ms": 1783827000000,
        "trade_time": "2026-07-12T03:30:00.000Z",
        "side": "SELL",
        "price": 100.0,
        "quantity": 1.0,
        "notional_usd": 100.0,
        "source": "binance_usdm_forceOrder_websocket",
        "is_real_liquidation_feed": True,
    }
    rows = [{**base, "symbol": "BTCUSDT"}, {**base, "symbol": "TUSDT"}]
    path = Path(args.data_dir) / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    report = build_report(args)

    assert report["events"]["events"] == 2
    assert report["events"]["research_universe"]["events"] == 1
    assert report["events"]["preregistered_sample"]["events"] == 1
    assert report["decision"] == "liquidation_force_order_collecting_insufficient_sample"
    assert report["ready_for_preregistered_research"] is False
    assert report["inputs"]["min_events_for_research"] == 2
    tmp.cleanup()


def test_evaluation_clock_is_captured_after_storage_scan(monkeypatch, tmp_path: Path):
    args = Args()
    args.data_dir = str(tmp_path / "data")
    args.collector_status = str(tmp_path / "status.json")
    args.collector_heartbeat = str(tmp_path / "heartbeat.json")
    args.latest_collector_report = str(tmp_path / "latest.json")
    args.contract = str(tmp_path / "contract.json")
    args.prereg_lock = str(tmp_path / "prereg.json")
    write_json(Path(args.collector_status), {"ts": "2026-07-14T12:00:00Z", "status": "running_collector_cycle", "pid": os.getpid()})
    write_json(Path(args.collector_heartbeat), {"ts": "2026-07-14T12:00:00Z", "can_trade": False, "liveness_messages_seen": 1})
    write_json(Path(args.latest_collector_report), {"decision": "live", "stats": {"parse_errors": [], "liveness_messages_seen": 1}})
    write_json(Path(args.contract), {"can_trade": False})
    write_prereg(Path(args.prereg_lock), "2026-07-14T11:00:00Z")
    row = {
        "event_time_ms": 1784030430000,
        "event_time": "2026-07-14T12:00:30.000Z",
        "trade_time_ms": 1784030430000,
        "trade_time": "2026-07-14T12:00:30.000Z",
        "symbol": "BTCUSDT",
        "side": "SELL",
        "price": 100.0,
        "quantity": 1.0,
        "notional_usd": 100.0,
        "source": "binance_usdm_forceOrder_websocket",
        "is_real_liquidation_feed": True,
    }
    times = iter(
        [
            datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 14, 12, 1, 0, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(module, "now_utc", lambda: next(times))
    monkeypatch.setattr(module, "read_jsonl_rows", lambda *_: ([row], [], []))

    report = module.build_report(args)

    assert report["events"]["last_event_age_minutes"] == 0.5
    assert report["scan"]["duration_seconds"] == 60.0
    assert report["scan"]["evaluation_clock_captured_after_storage_read"] is True

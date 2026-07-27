from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import liquidation_cross_venue_canonical_paired_leadership_forward_observer_v4 as module


PARENT = module.ROOT / "configs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_PREREG_V3_2026-07-13.json"


def prereg(tmp_path: Path, floor: str = "2099-01-02T00:00:00Z") -> dict:
    parent = module.base.read_json(PARENT)
    payload = copy.deepcopy(parent)
    payload.update(
        {
            "schema_version": 4,
            "prereg_id": "canonical_paired_v4_test",
            "status": "prospective_preregistration_before_forward_floor",
            "created_at": "2099-01-01T00:00:00Z",
            "forward_floor_at": floor,
            "sources": {"binance": str(tmp_path / "binance"), "bybit": str(tmp_path / "bybit")},
            "supersedes": {
                "lock_id": "liquidation_cross_venue_canonical_paired_receipt_leadership_2026_07_13_v3",
                "preregistration_path": module.base.portable(PARENT),
                "preregistration_sha256": module.base.sha256_file(PARENT),
                "reason": "operational ingest-contract rollover only",
                "strategy_parameters_changed": False,
                "outcomes_admitted": False,
            },
        }
    )
    rules = payload["fixed_rules"]
    rules.pop("required_ingest_schema_version")
    rules.update(
        {
            "required_ingest_schema_versions": {"binance": 2, "bybit": 4},
            "required_sources": {
                "binance": "binance_usdm_forceOrder_websocket",
                "bybit": "bybit_v5_allLiquidation_websocket",
            },
            "bybit_packet_identity": module.PACKET_IDENTITY,
            "bybit_market_tuple_deduplication": False,
        }
    )
    payload["research_boundary"].update(
        {
            "v3_observations_admitted": False,
            "pre_v4_bybit_rows_admitted": False,
            "outcomes_inherited_from_v3": False,
        }
    )
    return payload


def write_rows(path: Path, rows: list[dict]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "events.jsonl").write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8"
    )


def test_prereg_inherits_analytical_contract_and_lock_is_bound(tmp_path: Path) -> None:
    payload = prereg(tmp_path)
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(payload), encoding="utf-8")
    assert module.validate_prereg(payload) == []
    lock = module.build_lock(prereg_path, created_at="2099-01-01T12:00:00Z")
    assert lock["fixed_rules"]["required_ingest_schema_versions"] == {"binance": 2, "bybit": 4}
    assert lock["supersedes"]["strategy_parameters_changed"] is False
    assert len(lock["dependencies"]) == 3
    assert module.validate_lock(lock) == []


def test_changed_terminal_gate_is_rejected_as_retune(tmp_path: Path) -> None:
    payload = prereg(tmp_path)
    payload["terminal_gate"]["minimum_primary_window_pairs"] += 1
    assert "terminal_gate_changed" in module.validate_prereg(payload)


def test_loader_accepts_venue_specific_schemas_and_preserves_packet_items(tmp_path: Path) -> None:
    common = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "price": 100.0,
        "quantity": 2.0,
        "notional_usd": 200.0,
        "is_real_liquidation_feed": True,
        "collector_host": "TEST",
    }
    write_rows(
        tmp_path / "binance",
        [
            {
                **common,
                "received_at_ns": 10_000_000_000,
                "trade_time_ms": 9_000,
                "source": "binance_usdm_forceOrder_websocket",
                "ingest_schema_version": 2,
            }
        ],
    )
    bybit_rows = []
    for index in (0, 1):
        bybit_rows.append(
            {
                **common,
                "received_at_ns": 11_000_000_000 + index,
                "liquidation_time_ms": 9_500,
                "source": "bybit_v5_allLiquidation_websocket",
                "ingest_schema_version": 4,
                "collector_session_id": "session-a",
                "packet_sequence": 7,
                "packet_item_index": index,
                "packet_item_count": 2,
            }
        )
    bybit_rows.append(
        {
            **bybit_rows[0],
            "received_at_ns": 12_000_000_000,
            "source": "bybit_v5_allLiquidation_websocket_packet_ordinal_v4",
            "packet_sequence": 8,
            "packet_item_count": 1,
        }
    )
    write_rows(tmp_path / "bybit", bybit_rows)

    binance, binance_counts = module.load_events(
        "binance",
        tmp_path / "binance",
        floor_ns=0,
        symbols={"BTCUSDT"},
        required_host="TEST",
        required_schema_version=2,
        required_source="binance_usdm_forceOrder_websocket",
    )
    bybit, bybit_counts = module.load_events(
        "bybit",
        tmp_path / "bybit",
        floor_ns=0,
        symbols={"BTCUSDT"},
        required_host="TEST",
        required_schema_version=4,
        required_source="bybit_v5_allLiquidation_websocket",
    )
    assert len(binance) == 1
    assert binance_counts["accepted"] == 1
    assert len(bybit) == 2
    assert bybit_counts["accepted"] == 2
    assert bybit_counts["wrong_source"] == 1
    assert "duplicate_physical_event" not in bybit_counts


def test_duplicate_packet_identity_is_rejected(tmp_path: Path) -> None:
    row = {
        "symbol": "BTCUSDT",
        "side": "SELL",
        "received_at_ns": 10_000_000_000,
        "notional_usd": 1.0,
        "is_real_liquidation_feed": True,
        "collector_host": "TEST",
        "source": "bybit_v5_allLiquidation_websocket",
        "ingest_schema_version": 4,
        "collector_session_id": "session-a",
        "packet_sequence": 1,
        "packet_item_index": 0,
        "packet_item_count": 1,
    }
    write_rows(tmp_path / "bybit", [row, row])
    rows, counts = module.load_events(
        "bybit",
        tmp_path / "bybit",
        floor_ns=0,
        symbols={"BTCUSDT"},
        required_host="TEST",
        required_schema_version=4,
        required_source="bybit_v5_allLiquidation_websocket",
    )
    assert len(rows) == 1
    assert counts["duplicate_physical_event"] == 1


def test_run_before_floor_is_empty_and_non_trading(tmp_path: Path) -> None:
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg(tmp_path)), encoding="utf-8")
    lock = module.build_lock(prereg_path, created_at="2099-01-01T12:00:00Z")
    lock_path = tmp_path / "lock.json"
    module.base.write_json(lock_path, lock)
    report = module.run_observer(lock_path, tmp_path / "report", tmp_path / "terminal.json")
    assert report["decision"].endswith("waiting_forward_floor")
    assert report["primary_sample"]["matched_pairs"] == 0
    assert report["side_contract"]["v3_observations_admitted"] is False
    assert report["runtime_boundary"]["orders_allowed"] is False
    assert report["can_trade"] is False


def test_lock_cannot_be_sealed_at_or_after_floor(tmp_path: Path) -> None:
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg(tmp_path)), encoding="utf-8")
    with pytest.raises(ValueError, match="before forward_floor_at"):
        module.build_lock(prereg_path, created_at="2099-01-02T00:00:00Z")

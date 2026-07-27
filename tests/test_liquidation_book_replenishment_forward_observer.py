from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tools.liquidation_book_replenishment_forward_observer import run_observer, validate_lock


def ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1000)


def write_event(path: Path, timestamp: str, notional: float, side: str = "SELL") -> None:
    event_ms = ms(timestamp)
    row = {
        "event_time_ms": event_ms,
        "event_time": timestamp,
        "trade_time_ms": event_ms - 100,
        "symbol": "BTCUSDT",
        "side": side,
        "price": 100.0,
        "quantity": notional / 100.0,
        "notional_usd": notional,
        "is_real_liquidation_feed": True,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def insert_feature(
    connection: sqlite3.Connection,
    venue: str,
    product: str,
    timestamp: str,
    *,
    price_first: float,
    price_last: float,
    spread: float | None,
    imbalance: float | None,
    book_snapshots: int,
) -> None:
    connection.execute(
        "insert into minute_features values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            venue,
            product,
            ms(timestamp),
            10,
            1000.0,
            price_first,
            price_last,
            (price_last / price_first - 1.0) * 10_000.0,
            600.0,
            400.0,
            200.0,
            1,
            book_snapshots,
            spread,
            imbalance,
        ),
    )


def create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        create table minute_features (
          venue text not null,
          product text not null,
          minute_ms integer not null,
          trades integer not null,
          notional real not null,
          price_first real,
          price_last real,
          return_bps real,
          buy_notional real,
          sell_notional real,
          delta_notional real,
          aggressor_side_usable integer not null,
          book_snapshots integer not null,
          avg_spread_bps real,
          avg_top_imbalance real,
          primary key (venue, minute_ms)
        )
        """
    )
    for timestamp in ("2026-01-01T00:28:00Z", "2026-01-01T00:29:00Z"):
        insert_feature(
            connection,
            "binance",
            "BTCUSDT",
            timestamp,
            price_first=100.0,
            price_last=100.0,
            spread=1.0,
            imbalance=-0.10,
            book_snapshots=5,
        )
    for timestamp in ("2026-01-01T00:31:00Z", "2026-01-01T00:32:00Z"):
        insert_feature(
            connection,
            "binance",
            "BTCUSDT",
            timestamp,
            price_first=100.0,
            price_last=100.0,
            spread=1.0,
            imbalance=0.20,
            book_snapshots=5,
        )
        insert_feature(
            connection,
            "coinbase",
            "BTC-USD",
            timestamp,
            price_first=100.0,
            price_last=100.0,
            spread=None,
            imbalance=None,
            book_snapshots=0,
        )
    insert_feature(
        connection,
        "binance",
        "BTCUSDT",
        "2026-01-01T00:33:00Z",
        price_first=100.0,
        price_last=101.0,
        spread=1.0,
        imbalance=0.10,
        book_snapshots=5,
    )
    connection.commit()
    connection.close()


def locked_config() -> dict:
    return {
        "lock_id": "synthetic_lock",
        "status": "prospective_forward_lock_before_outcome_review",
        "forward_start_at": "2026-01-01T00:00:00Z",
        "can_trade": False,
        "orders_allowed": False,
        "fixed_rules": {
            "symbol": "BTCUSDT",
            "minimum_prior_event_minutes": 2,
            "rolling_event_minutes": 1440,
            "burst_notional_quantile": 0.5,
            "minimum_burst_notional_usd": 100.0,
            "minimum_side_dominance_ratio": 1.5,
            "pre_event_minutes": 2,
            "confirmation_minutes": 2,
            "minimum_post_book_minutes": 2,
            "minimum_abs_post_imbalance": 0.05,
            "minimum_imbalance_recovery": 0.10,
            "maximum_spread_ratio_to_pre": 1.25,
            "coinbase_nonconfirmation_bps": 5.0,
            "outcome_horizons_minutes": [1],
            "fee_and_slippage_bps_per_side": 7.0,
            "side_mapping": {"SELL": "LONG", "BUY": "SHORT"},
        },
        "forward_gate": {
            "minimum_resolved_events_per_horizon": 2,
            "minimum_positive_horizons": 1,
            "minimum_mean_net_bps": 5.0,
            "minimum_winrate_pct": 52.0,
            "minimum_profit_factor": 1.05,
        },
        "runtime_boundary": {
            "observer_only": True,
            "research_only": True,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }


def test_forward_lock_filters_history_and_ledger_is_idempotent(tmp_path: Path) -> None:
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(locked_config()), encoding="utf-8")
    liquidation_root = tmp_path / "liquidations"
    symbol_root = liquidation_root / "BTCUSDT"
    symbol_root.mkdir(parents=True)
    event_path = symbol_root / "events.jsonl"
    write_event(event_path, "2025-12-31T23:59:00Z", 50_000.0)
    write_event(event_path, "2026-01-01T00:10:00Z", 100.0)
    write_event(event_path, "2026-01-01T00:20:00Z", 200.0)
    write_event(event_path, "2026-01-01T00:30:00Z", 1_000.0)
    database = tmp_path / "microstructure.sqlite3"
    create_database(database)
    ledger = tmp_path / "outcomes.jsonl"
    out_prefix = tmp_path / "report"

    first = run_observer(
        lock_path=lock_path,
        liquidation_root=liquidation_root,
        database=database,
        ledger_path=ledger,
        out_prefix=out_prefix,
    )
    assert first["sample"]["event_minutes"] == 3
    assert first["sample"]["signals"] == 1
    assert first["sample"]["new_outcome_rows"] == 1
    assert first["sample"]["resolved_outcome_rows"] == 1
    assert first["decision"] == "liquidation_book_replenishment_collecting_resolved_outcomes"
    ledger_row = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert ledger_row["event_time"] == "2026-01-01T00:30:00.000Z"
    assert ledger_row["observer_side"] == "LONG"
    assert ledger_row["net_bps"] > 80.0
    assert ledger_row["can_trade"] is False

    second = run_observer(
        lock_path=lock_path,
        liquidation_root=liquidation_root,
        database=database,
        ledger_path=ledger,
        out_prefix=out_prefix,
    )
    assert second["sample"]["new_outcome_rows"] == 0
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_lock_fails_closed_when_trading_boundary_is_enabled() -> None:
    lock = locked_config()
    lock["can_trade"] = True
    assert "top_level_runtime_boundary" in validate_lock(lock)

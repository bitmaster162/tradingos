import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.post_fill_markout_forward_runtime import (
    _build_bot_config,
    contiguous_user_trade_coverage_cursor,
    run_pulse,
)
from btcusdt_bot.monitoring.post_fill_forward import load_post_fill_forward_lock


ROOT = Path(__file__).resolve().parents[1]


def _bucket(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _write_lock(tmp_path: Path, *, floor_ms: int) -> Path:
    path = tmp_path / "configs" / "lock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "lock_id": "runtime_test_lock",
                "status": "forward_execution_quality_preregistered",
                "forward_start_ms": floor_ms,
                "data_contract": {
                    "symbol": "BTCUSDT",
                    "archive_root": "data/post_fill_markout_forward",
                    "market_root": "data/post_fill_markout_forward",
                    "reference_source": "book_mid",
                },
                "timing": {
                    "primary_horizon_seconds": 30,
                    "secondary_horizons_seconds": [5, 300],
                    "max_pre_fill_age_ms": 5_000,
                    "max_post_horizon_delay_ms": 5_000,
                    "max_capture_event_age_ms": 120_000,
                },
                "evidence_floor": {
                    "minimum_evaluated_fills_per_horizon": 100,
                    "minimum_distinct_utc_days": 3,
                    "minimum_evaluation_coverage_ratio": 1.0,
                },
                "runtime_boundary": {
                    "signals_allowed": False,
                    "paper_entries_allowed": False,
                    "orders_allowed": False,
                    "automatic_promotion_allowed": False,
                    "can_trade": False,
                },
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_capture(tmp_path: Path, *, floor_ms: int, generated_at_ms: int) -> None:
    path = (
        tmp_path
        / "data"
        / "post_fill_markout_forward"
        / "public"
        / _bucket(floor_ms)
        / "btcusdt_bookTicker.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "received_at_ms": timestamp_ms + 1,
            "payload": {
                "e": "bookTicker",
                "E": timestamp_ms,
                "s": "BTCUSDT",
                "b": "99",
                "a": "101",
            },
        }
        for timestamp_ms in (floor_ms - 1, generated_at_ms - 50)
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_contiguous_cursor_stops_at_first_manifest_gap(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "datasets": {
                    "user_trades": {
                        "day1": {"intervals": [[100, 199], [200, 299]]},
                        "day2": {"intervals": [[400, 499]]},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert contiguous_user_trade_coverage_cursor(manifest, floor_ms=100) == 300


def test_pulse_without_credentials_is_observer_only(monkeypatch, tmp_path: Path) -> None:
    floor_ms = 1_700_000_000_000
    generated_at_ms = floor_ms + 400_000
    prereg = _write_lock(tmp_path, floor_ms=floor_ms)
    _write_capture(tmp_path, floor_ms=floor_ms, generated_at_ms=generated_at_ms)
    monkeypatch.setenv("BOT_ENV", "demo")
    monkeypatch.setenv("BOT_SYMBOL", "BTCUSDT")
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    lock = load_post_fill_forward_lock(prereg, project_root=tmp_path)
    config = _build_bot_config(lock)

    pulse = run_pulse(
        prereg_path=prereg,
        project_root=tmp_path,
        config=config,
        generated_at_ms=generated_at_ms,
        collector_status={"messages_received": 2, "can_trade": False},
    )

    assert pulse["backfill"]["decision"] == "authoritative_backfill_blocked_credentials_missing"
    assert pulse["backfill"]["request_sent"] is False
    assert pulse["backfill"]["endpoint_scope"] == ["/fapi/v1/userTrades"]
    assert pulse["backfill"]["income_requested"] is False
    assert pulse["observer"]["decision"] == "waiting_demo_credentials_for_authoritative_fills"
    assert pulse["runtime_boundary"]["orders_allowed"] is False
    assert pulse["can_trade"] is False
    assert (tmp_path / "data" / "live" / "reports" / "latest_post_fill_forward_observer.json").exists()


def test_runtime_rejects_live_environment(monkeypatch, tmp_path: Path) -> None:
    prereg = _write_lock(tmp_path, floor_ms=1_700_000_000_000)
    lock = load_post_fill_forward_lock(prereg, project_root=tmp_path)
    monkeypatch.setenv("BOT_ENV", "live")

    with pytest.raises(ValueError, match="requires_demo_env"):
        _build_bot_config(lock)


def test_runtime_source_has_no_execution_or_order_submission_surface() -> None:
    source = (ROOT / "tools" / "post_fill_markout_forward_runtime.py").read_text(encoding="utf-8")

    assert "execution.gateway" not in source
    assert "ExecutionGateway" not in source
    assert "new_order" not in source
    assert "place_order" not in source
    assert 'signed_endpoint_allowlist": ["/fapi/v1/userTrades"]' in source
    assert '"orders_allowed": False' in source
    assert '"can_trade": False' in source

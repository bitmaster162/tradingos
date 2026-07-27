from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.exogenous_liquidity_regime_forward_observer import run_observer


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def prereg() -> dict:
    return {
        "hypothesis_id": "test-exogenous",
        "status": "prospective_forward_preregistration_before_outcomes",
        "registered_at": "2026-01-01T00:00:00Z",
        "forward_floor_at": "2026-01-01T00:00:00Z",
        "baseline_macro_proxy_date": "2026-01-01",
        "causal_alignment": {
            "historical_rows_for_strategy_selection_allowed": False,
            "pre_floor_records_allowed": False,
            "lookahead_allowed": False,
        },
        "fixed_rules": {
            "registered_configurations": 1,
            "primary_horizon_hours": 168,
            "fee_and_slippage_bps_per_side": 10.0,
            "positive_threshold_bps": 0.0,
            "negative_threshold_bps": 0.0,
            "risk_on_side": "LONG",
            "risk_off_side": "SHORT_RESEARCH_ONLY",
            "maximum_weighted_depeg_deviation_bps": 20.0,
            "maximum_assets_over_50bps": 0,
        },
        "forward_gate": {
            "minimum_resolved_aligned_events": 26,
            "minimum_long_events": 8,
            "minimum_short_events": 8,
            "minimum_unique_macro_dates": 26,
            "minimum_span_days": 182,
            "minimum_mean_net_bps": 20.0,
            "minimum_winrate_pct": 52.0,
            "minimum_profit_factor": 1.1,
            "minimum_side_mean_net_bps": 0.0,
            "retuning_allowed": False,
        },
        "source_freshness": {"maximum_readiness_report_age_hours": 30.0},
        "runtime_boundary": {
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def readiness(generated_at: str) -> dict:
    return {
        "generated_at": generated_at,
        "decision": "forward_data_collecting",
        "research_gate_ready": False,
        "lock_verified": True,
        "collector_integrity": {"passed": True},
        "metrics": {"new_unique_source_dates": 1, "new_unique_weekly_dates": 1},
        "can_trade": False,
    }


def stable_row(collected_at: str, change_bps: float = 10.0) -> dict:
    return {
        "collected_at": collected_at,
        "aggregate_change_7d": {"change_bps": change_bps},
        "depeg_guard": {"weighted_absolute_deviation_bps": 2.0, "assets_over_50bps": 0},
        "historical_chart": {"latest_date": 1767830400},
        "quality_pass": True,
        "metric_semantics": "global_supply_not_exchange_netflow",
        "can_trade": False,
    }


def macro_row(collected_at: str, proxy_date: str, change_bps: float = 20.0) -> dict:
    return {
        "collected_at": collected_at,
        "latest_proxy_date": proxy_date,
        "changes": {"4w": {"change_bps": change_bps}},
        "quality_pass": True,
        "proxy_semantics": "heuristic_fed_assets_minus_tga_minus_on_rrp",
        "can_trade": False,
    }


def write_btc(path: Path) -> None:
    entry = datetime(2026, 1, 8, 7, tzinfo=timezone.utc)
    exit_time = entry + timedelta(hours=168)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time", "time_ms", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for timestamp, price in ((entry, 100.0), (exit_time, 110.0)):
            writer.writerow(
                {
                    "time": timestamp.isoformat(),
                    "time_ms": int(timestamp.timestamp() * 1000),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 1.0,
                }
            )


def run_fixture(tmp_path: Path, *, readiness_time: str) -> dict:
    prereg_path = tmp_path / "prereg.json"
    stable_path = tmp_path / "stable.jsonl"
    macro_path = tmp_path / "macro.jsonl"
    stable_readiness = tmp_path / "stable_readiness.json"
    macro_readiness = tmp_path / "macro_readiness.json"
    btc_path = tmp_path / "btc.csv"
    events = tmp_path / "events.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    write_json(prereg_path, prereg())
    write_json(stable_readiness, readiness(readiness_time))
    write_json(macro_readiness, readiness(readiness_time))
    write_jsonl(
        stable_path,
        [
            stable_row("2025-12-31T23:00:00Z"),
            stable_row("2026-01-08T05:00:00Z"),
        ],
    )
    write_jsonl(
        macro_path,
        [
            macro_row("2026-01-02T06:00:00Z", "2026-01-01"),
            macro_row("2026-01-08T06:10:00Z", "2026-01-08"),
        ],
    )
    write_btc(btc_path)
    kwargs = {
        "prereg_path": prereg_path,
        "stable_metrics_path": stable_path,
        "macro_metrics_path": macro_path,
        "stable_readiness_path": stable_readiness,
        "macro_readiness_path": macro_readiness,
        "btc_path": btc_path,
        "event_ledger_path": events,
        "outcome_ledger_path": outcomes,
        "out_prefix": tmp_path / "report",
        "observed_at": datetime(2026, 1, 8, 8, tzinfo=timezone.utc),
    }
    first = run_observer(**kwargs)
    first["_paths"] = {"events": events, "outcomes": outcomes}
    first["_kwargs"] = kwargs
    return first


def test_forward_only_alignment_and_outcome_are_idempotent(tmp_path: Path) -> None:
    first = run_fixture(tmp_path, readiness_time="2026-01-08T07:30:00Z")
    assert first["sample"]["events_total"] == 1
    assert first["sample"]["aligned_events"] == 1
    assert first["sample"]["resolved_outcomes"] == 1
    assert first["decision"] == "exogenous_liquidity_regime_collecting_forward_sample"
    event = json.loads(first["_paths"]["events"].read_text(encoding="utf-8").strip())
    outcome = json.loads(first["_paths"]["outcomes"].read_text(encoding="utf-8").strip())
    assert event["macro_proxy_date"] == "2026-01-08"
    assert event["side"] == "LONG"
    assert event["entry_time"] == "2026-01-08T07:00:00.000Z"
    assert outcome["net_bps"] == 980.0
    assert outcome["can_trade"] is False

    second = run_observer(**first["_kwargs"])
    assert second["sample"]["events_added"] == 0
    assert second["sample"]["outcomes_added"] == 0
    assert len(first["_paths"]["events"].read_text(encoding="utf-8").splitlines()) == 1


def test_stale_readiness_fails_closed_before_event_ledger(tmp_path: Path) -> None:
    report = run_fixture(tmp_path, readiness_time="2026-01-01T00:00:00Z")
    assert report["decision"] == "exogenous_liquidity_regime_blocked_source_integrity_or_freshness"
    assert report["sample"]["events_total"] == 0
    assert not report["_paths"]["events"].exists()
    assert report["can_trade"] is False

from __future__ import annotations

import json
from pathlib import Path

from tools.portfolio_scenario_stress_guard import build_report


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def policy() -> dict:
    return {
        "thresholds": {
            "max_stressed_mmr_pct": 80.0,
            "max_equity_drawdown_pct": 25.0,
            "min_stressed_equity_usd": 100.0,
        },
        "scenarios": [{"id": "down", "returns_pct": {"BTC": -10.0, "USDT": 0.0}}],
        "assumptions": [],
    }


def portfolio() -> dict:
    return {
        "starting_equity_usd": 10000.0,
        "collateral": [
            {"asset": "USDT", "market_value_usd": 5000.0, "haircut_pct": 0.0},
        ],
        "derivative_positions": [
            {
                "symbol": "BTCUSDT_LINEAR",
                "underlying": "BTC",
                "quantity": 1.0,
                "mark_price_usd": 1000.0,
                "contract_multiplier": 1.0,
                "maintenance_rate_pct": 1.0,
            }
        ],
    }


def build(tmp_path: Path, policy_payload: dict, portfolio_payload: dict) -> dict:
    policy_path = tmp_path / "policy.json"
    portfolio_path = tmp_path / "portfolio.json"
    write_json(policy_path, policy_payload)
    write_json(portfolio_path, portfolio_payload)
    return build_report(policy_path, portfolio_path)


def test_linear_long_loses_on_down_scenario_and_guard_stays_non_trading(tmp_path: Path) -> None:
    report = build(tmp_path, policy(), portfolio())

    scenario = report["scenarios"][0]
    assert scenario["summary"]["derivative_pnl_usd"] == -100.0
    assert scenario["summary"]["stressed_maintenance_margin_usd"] == 9.0
    assert report["decision"] == "portfolio_scenario_stress_guard_passed_research_only"
    assert report["model_boundary"]["exchange_wce_replica"] is False
    assert report["can_trade"] is False


def test_short_position_gains_when_underlying_falls(tmp_path: Path) -> None:
    payload = portfolio()
    payload["derivative_positions"][0]["quantity"] = -1.0
    report = build(tmp_path, policy(), payload)

    assert report["scenarios"][0]["summary"]["derivative_pnl_usd"] == 100.0
    assert report["can_trade"] is False


def test_missing_asset_shock_blocks_instead_of_assuming_zero(tmp_path: Path) -> None:
    policy_payload = policy()
    policy_payload["scenarios"][0]["returns_pct"].pop("BTC")
    report = build(tmp_path, policy_payload, portfolio())

    assert report["decision"] == "portfolio_scenario_stress_guard_invalid_input_blocked"
    assert "scenario_asset_missing_or_invalid:down:BTC" in report["validation_errors"]
    assert report["scenarios"] == []
    assert report["can_trade"] is False


def test_drawdown_breach_fails_closed(tmp_path: Path) -> None:
    policy_payload = policy()
    policy_payload["thresholds"]["max_equity_drawdown_pct"] = 5.0
    payload = portfolio()
    payload["derivative_positions"][0]["quantity"] = 10.0
    report = build(tmp_path, policy_payload, payload)

    assert report["decision"] == "portfolio_scenario_stress_guard_breached_blocked"
    assert report["scenarios"][0]["checks"]["drawdown_within_limit"] is False
    assert report["summary"]["breached_scenarios"] == ["down"]
    assert report["can_trade"] is False


def test_mmr_breach_fails_closed_even_without_price_loss(tmp_path: Path) -> None:
    policy_payload = policy()
    policy_payload["thresholds"]["max_stressed_mmr_pct"] = 5.0
    policy_payload["scenarios"][0]["returns_pct"]["BTC"] = 0.0
    payload = portfolio()
    payload["derivative_positions"][0]["quantity"] = 100.0
    report = build(tmp_path, policy_payload, payload)

    scenario = report["scenarios"][0]
    assert scenario["summary"]["equity_drawdown_pct"] == 0.0
    assert scenario["summary"]["stressed_mmr_pct"] == 10.0
    assert scenario["checks"]["mmr_within_limit"] is False
    assert report["decision"] == "portfolio_scenario_stress_guard_breached_blocked"
    assert report["can_trade"] is False

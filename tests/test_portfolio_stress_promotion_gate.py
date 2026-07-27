from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.portfolio_scenario_stress_guard import build_report as build_stress
from tools.portfolio_stress_promotion_gate import build_report as build_gate


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def snapshot(now: datetime, *, synthetic: bool = False) -> dict:
    return {
        "snapshot_id": "paper_snapshot_1",
        "snapshot_kind": "paper_account",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "source_mode": "local_paper_state",
        "synthetic": synthetic,
        "can_trade": False,
        "starting_equity_usd": 10000.0,
        "collateral": [{"asset": "USDT", "market_value_usd": 10000.0, "haircut_pct": 0.0}],
        "derivative_positions": [],
    }


def make_stress(tmp_path: Path, snapshot_payload: dict) -> tuple[Path, Path]:
    portfolio_path = tmp_path / "paper_snapshot.json"
    stress_path = tmp_path / "stress.json"
    write_json(portfolio_path, snapshot_payload)
    report = build_stress(ROOT / "configs/PORTFOLIO_SCENARIO_STRESS_POLICY_v1.json", portfolio_path)
    write_json(stress_path, report)
    return portfolio_path, stress_path


def test_fresh_hash_bound_paper_snapshot_allows_design_review_only(tmp_path: Path) -> None:
    _, stress_path = make_stress(tmp_path, snapshot(datetime.now(timezone.utc)))
    report = build_gate(stress_path)

    assert report["decision"] == "portfolio_stress_promotion_gate_passed_manual_review_only"
    assert report["promotion"]["paper_design_review_allowed"] is True
    assert report["promotion"]["paper_execution_allowed"] is False
    assert report["promotion"]["live_execution_allowed"] is False
    assert report["can_trade"] is False


def test_synthetic_snapshot_is_blocked(tmp_path: Path) -> None:
    _, stress_path = make_stress(tmp_path, snapshot(datetime.now(timezone.utc), synthetic=True))
    report = build_gate(stress_path)

    assert report["checks"]["snapshot_not_synthetic"] is False
    assert report["promotion"]["paper_design_review_allowed"] is False
    assert report["can_trade"] is False


def test_stale_snapshot_is_blocked(tmp_path: Path) -> None:
    stale = datetime.now(timezone.utc) - timedelta(hours=25)
    _, stress_path = make_stress(tmp_path, snapshot(stale))
    report = build_gate(stress_path, max_snapshot_age_hours=24.0)

    assert report["checks"]["snapshot_not_too_old"] is False
    assert report["promotion"]["paper_design_review_allowed"] is False


def test_snapshot_tamper_after_stress_is_blocked(tmp_path: Path) -> None:
    portfolio_path, stress_path = make_stress(tmp_path, snapshot(datetime.now(timezone.utc)))
    payload = json.loads(portfolio_path.read_text(encoding="utf-8"))
    payload["starting_equity_usd"] = 1.0
    write_json(portfolio_path, payload)
    report = build_gate(stress_path)

    assert report["checks"]["snapshot_hash_matches"] is False
    assert report["promotion"]["paper_design_review_allowed"] is False
    assert report["can_trade"] is False

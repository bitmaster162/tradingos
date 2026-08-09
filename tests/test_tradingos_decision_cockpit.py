from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "tools" / "tradingos_decision_cockpit.py"
spec = importlib.util.spec_from_file_location("cockpit", MODULE)
assert spec and spec.loader
cockpit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cockpit)


def sample_brief() -> dict:
    return {
        "brief_id":"b1","snapshot_id":"s1","symbol":"BTCUSDT","timeframe":"4h","as_of":"2026-08-09T16:00:00Z","status":"READY","can_trade":False,
        "decision":{"stance":"WATCH_LONG","score_margin":4.5},
        "regime":{"label":"TREND_UP","volatility":"COMPRESSED"},
        "intent_hypotheses":[
            {"direction":"LONG","support_score":5.5,"counter_score":1.0,"independent_support_dimensions":4,"supporting_evidence":[
                {"dimension":"market_structure","label":"HTF trend","direction":"LONG","strength":2.0,"observation":"trend=up"},
                {"dimension":"open_interest","label":"Price/OI alignment","direction":"LONG","strength":1.25,"observation":"price up / OI up"}],"contradicting_evidence":[]},
            {"direction":"SHORT","support_score":1.0,"counter_score":5.5,"independent_support_dimensions":1,"supporting_evidence":[
                {"dimension":"derivatives_crowding","label":"Positive crowding","direction":"SHORT","strength":1.0,"observation":"basis z=1.88"}],"contradicting_evidence":[]}],
        "derivatives_context":{"open_interest_change_pct":1.07,"funding_rate":0.00007,"funding_z":0.65,"basis_pct":-0.03,"basis_z":1.88,"liquidation_bias":"not_observed"},
        "scenarios":[
            {"name":"bull","trigger":"4h close above 65358","invalidation":"close back below","operator_use":"reassess only"},
            {"name":"base","trigger":"inside range","invalidation":"close outside","operator_use":"wait"},
            {"name":"bear","trigger":"4h close below 64111","invalidation":"reclaim","operator_use":"reassess only"}],
        "uncertainty":{"snapshot_age_minutes":0.0,"missing_data":[],"conflicts":[],"blockers":[]},
        "operator_next_action":"Wait for confirmation; do not place an order from this brief.",
        "provenance":{"input_sources":[1,2,3,4]},
        "permissions":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY"},
    }


def sample_snapshot() -> dict:
    return {"price":{"last":65207.7},"market_structure":{"support":64111.0,"resistance":65358.0,"range_position":0.8795},
            "derivatives":{"open_interest_change_pct":1.0695,"funding_z":0.6459,"basis_z":1.8843},
            "flow":{"spot_cvd_direction":"up","perp_cvd_direction":"up","relative_volume":0.8503}}


def test_first_observation_cockpit_is_safe_and_complete(tmp_path: Path) -> None:
    report = cockpit.build_report(sample_brief(), sample_snapshot())
    assert report["delta"]["state"] == "FIRST_OBSERVATION"
    assert report["executive"]["stance"] == "WATCH_LONG"
    assert report["safety"]["orders_allowed"] is False
    assert report["safety"]["signals_allowed"] is False
    assert report["safety"]["can_trade"] is False
    assert len(report["scenarios"]) == 3
    assert any(x["label"] == "Relative basis extreme" for x in report["risk_flags"])
    html = cockpit.render_html(report)
    assert "Decision Delta" in html
    assert "Pressure Map" in html
    assert "Scenario Ladder" in html
    assert "can_trade=false" in html


def test_delta_exposes_thesis_change_without_probability_claims() -> None:
    current_b = sample_brief(); current_s = sample_snapshot()
    prior_b = json.loads(json.dumps(current_b)); prior_s = json.loads(json.dumps(current_s))
    prior_b["decision"]["stance"] = "NO_ACTION"
    prior_b["decision"]["score_margin"] = 1.5
    prior_s["price"]["last"] = 64800.0
    prior_s["derivatives"]["open_interest_change_pct"] = -0.5
    report = cockpit.build_report(current_b, current_s, prior_b, prior_s)
    assert report["delta"]["state"] == "COMPARABLE"
    assert "changed" in report["delta"]["headline"].lower()
    assert any(row["metric"] == "stance" for row in report["delta"]["changes"])
    assert "probability" not in json.dumps(report).lower()


def test_unsafe_brief_fails_closed() -> None:
    brief = sample_brief(); brief["permissions"]["orders_allowed"] = True
    try:
        cockpit.build_report(brief, sample_snapshot())
    except ValueError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("unsafe execution permission accepted")

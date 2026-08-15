from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALERTS_PATH = ROOT / "tools" / "tradingos_decision_alerts.py"
COCKPIT_PATH = ROOT / "tools" / "tradingos_decision_cockpit.py"

alerts_spec = importlib.util.spec_from_file_location("alerts", ALERTS_PATH)
assert alerts_spec and alerts_spec.loader
alerts = importlib.util.module_from_spec(alerts_spec)
alerts_spec.loader.exec_module(alerts)

cockpit_spec = importlib.util.spec_from_file_location("cockpit", COCKPIT_PATH)
assert cockpit_spec and cockpit_spec.loader
cockpit_mod = importlib.util.module_from_spec(cockpit_spec)
cockpit_spec.loader.exec_module(cockpit_mod)


def cockpit() -> dict:
    return {
        "schema": "tradingos.decision_cockpit.v1",
        "version": "1.3.0",
        "brief_id": "b1",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "as_of": "2026-08-09T16:01:36Z",
        "status": "READY",
        "executive": {"stance": "WATCH_LONG", "next": "wait"},
        "levels": {"last": 65207.7, "support": 64111.0, "resistance": 65358.0},
        "risk_flags": [{"severity": "WATCH", "label": "Relative basis extreme", "detail": "z=1.88"}],
        "quality": {"blockers": []},
        "safety": {"signals": False, "orders": False, "signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def earlier(packet: dict) -> dict:
    value = json.loads(json.dumps(packet))
    value["brief_id"] = "b0"
    value["as_of"] = "2026-08-09T12:01:36Z"
    return value


def test_first_state_near_long_trigger_notifies() -> None:
    alert = alerts.build(cockpit())
    assert alert["decision"] == "NOTIFY"
    assert alert["priority"] == "HIGH"
    assert alert["level_state"] == "LONG_TRIGGER_ZONE"
    assert alert["timeframe"] == "4h"
    assert any(item["kind"] == "LEVEL_PROXIMITY" for item in alert["events"])
    assert alert["safety"]["can_trade"] is False


def test_identical_state_is_silent_with_strictly_earlier_previous() -> None:
    current = cockpit()
    previous = earlier(current)
    alert = alerts.build(current, previous)
    assert alert["decision"] == "SILENT"
    assert any(item["kind"] == "NO_MATERIAL_CHANGE" for item in alert["events"])


def test_stance_change_notifies() -> None:
    current = cockpit()
    previous = earlier(current)
    previous["executive"]["stance"] = "NO_ACTION"
    alert = alerts.build(current, previous)
    assert alert["decision"] == "NOTIFY"
    assert any(item["kind"] == "STANCE_CHANGE" for item in alert["events"])


def test_unsafe_cockpit_fails_closed() -> None:
    for field in ("orders_allowed", "signals_allowed", "can_trade", "orders", "signals"):
        current = cockpit()
        current["safety"][field] = True
        with pytest.raises(ValueError, match="unsafe"):
            alerts.build(current)
    current = cockpit()
    current["safety"]["capital_permission"] = "ALLOW"
    with pytest.raises(ValueError, match="unsafe"):
        alerts.build(current)


def test_wrong_schema_and_missing_identity_fail_closed() -> None:
    current = cockpit()
    current["schema"] = "other.schema"
    with pytest.raises(ValueError, match="schema"):
        alerts.build(current)

    for key in ("brief_id", "symbol", "timeframe", "as_of"):
        current = cockpit()
        current[key] = ""
        with pytest.raises(ValueError, match=key):
            alerts.build(current)


def test_comparison_identity_and_time_are_bound() -> None:
    current = cockpit()

    previous = earlier(current)
    previous["symbol"] = "ETHUSDT"
    with pytest.raises(ValueError, match="symbol mismatch"):
        alerts.build(current, previous)

    previous = earlier(current)
    previous["timeframe"] = "1h"
    with pytest.raises(ValueError, match="timeframe mismatch"):
        alerts.build(current, previous)

    previous = json.loads(json.dumps(current))
    with pytest.raises(ValueError, match="strictly earlier"):
        alerts.build(current, previous)

    previous = earlier(current)
    previous["as_of"] = "2026-08-09T20:01:36Z"
    with pytest.raises(ValueError, match="strictly earlier"):
        alerts.build(current, previous)


def test_timestamp_must_be_timezone_aware() -> None:
    current = cockpit()
    current["as_of"] = "2026-08-09T16:01:36"
    with pytest.raises(ValueError, match="timezone"):
        alerts.build(current)


def test_malformed_structures_fail_closed() -> None:
    mutations = [
        ("executive", []),
        ("levels", []),
        ("risk_flags", {}),
        ("quality", []),
        ("safety", []),
    ]
    for key, bad in mutations:
        current = cockpit()
        current[key] = bad
        with pytest.raises(ValueError):
            alerts.build(current)

    current = cockpit()
    current["risk_flags"] = ["bad"]
    with pytest.raises(ValueError, match="risk_flags entries"):
        alerts.build(current)

    current = cockpit()
    current["quality"]["blockers"] = [123]
    with pytest.raises(ValueError, match="blockers entries"):
        alerts.build(current)

    for field in ("last", "support", "resistance"):
        current = cockpit()
        current["levels"][field] = "bad"
        with pytest.raises(ValueError, match=f"levels.{field}"):
            alerts.build(current)


def test_dedupe_key_binds_symbol_and_timeframe() -> None:
    btc_4h = cockpit()
    eth_4h = cockpit(); eth_4h["symbol"] = "ETHUSDT"
    btc_1h = cockpit(); btc_1h["timeframe"] = "1h"
    assert alerts.build(btc_4h)["dedupe_key"] != alerts.build(eth_4h)["dedupe_key"]
    assert alerts.build(btc_4h)["dedupe_key"] != alerts.build(btc_1h)["dedupe_key"]


def test_new_risk_and_blocker_events_notify() -> None:
    current = cockpit()
    previous = earlier(current)
    previous["risk_flags"] = []
    current["quality"]["blockers"] = ["FRESHNESS_BLOCK"]
    alert = alerts.build(current, previous)
    kinds = {item["kind"] for item in alert["events"]}
    assert "NEW_RISK_FLAG" in kinds
    assert "NEW_BLOCKER" in kinds
    assert alert["decision"] == "NOTIFY"


def canonical_brief_and_snapshot() -> tuple[dict, dict]:
    brief = {
        "brief_id": "canonical-v2-b1",
        "snapshot_id": "canonical-s1",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "as_of": "2026-08-09T16:01:36Z",
        "status": "READY",
        "can_trade": False,
        "decision": {"stance": "WATCH_LONG", "score_margin": 4.5},
        "regime": {"label": "TREND_UP", "volatility": "COMPRESSED"},
        "intent_hypotheses": [
            {"direction": "LONG", "support_score": 5.5, "supporting_evidence": []},
            {"direction": "SHORT", "support_score": 1.0, "supporting_evidence": []},
        ],
        "derivatives_context": {"basis_z": 1.88},
        "scenarios": [],
        "uncertainty": {"snapshot_age_minutes": 0.0, "missing_data": [], "conflicts": [], "blockers": []},
        "operator_next_action": "Wait for confirmation; do not place an order from this brief.",
        "provenance": {
            "input_sources": [1, 2, 3, 4],
            "generator": "tools/tradingos_decision_brief_v2.py",
            "generator_version": "2.0.0",
        },
        "permissions": {
            "read_only_analysis": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "uses_credentials": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }
    snapshot = {
        "snapshot_id": "canonical-s1",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "as_of": "2026-08-09T16:01:36Z",
        "can_trade": False,
        "price": {"last": 65207.7},
        "market_structure": {"support": 64111.0, "resistance": 65358.0, "range_position": 0.8795},
        "derivatives": {"open_interest_change_pct": 1.0, "funding_z": 0.6, "basis_z": 1.88},
        "flow": {"spot_cvd_direction": "up", "perp_cvd_direction": "up", "relative_volume": 0.85},
    }
    return brief, snapshot


def test_exact_canonical_cockpit_v13_output_is_accepted() -> None:
    brief, snapshot = canonical_brief_and_snapshot()
    canonical_output = cockpit_mod.build_report(brief, snapshot)
    assert canonical_output["schema"] == "tradingos.decision_cockpit.v1"
    assert canonical_output["version"] == "1.3.0"
    alert = alerts.build(canonical_output)
    assert alert["schema"] == "tradingos.decision_alert.v1"
    assert alert["symbol"] == "BTCUSDT"
    assert alert["timeframe"] == "4h"
    assert alert["safety"]["signals_allowed"] is False
    assert alert["safety"]["orders_allowed"] is False
    assert alert["safety"]["can_trade"] is False
    assert alert["safety"]["capital_permission"] == "DENY"


def test_status_and_stance_must_be_nonempty_strings() -> None:
    current = cockpit(); current["status"] = ""
    with pytest.raises(ValueError, match="status"):
        alerts.build(current)
    current = cockpit(); current["executive"]["stance"] = ""
    with pytest.raises(ValueError, match="stance"):
        alerts.build(current)


def test_status_change_and_level_cross_are_preserved() -> None:
    current = cockpit(); previous = earlier(current)
    previous["status"] = "BLOCKED"
    previous["levels"]["last"] = 64000.0
    alert = alerts.build(current, previous)
    kinds = {item["kind"] for item in alert["events"]}
    assert "STATUS_CHANGE" in kinds
    assert "LEVEL_CROSS" in kinds


def test_alert_output_contains_no_probability_claim() -> None:
    alert = alerts.build(cockpit())
    assert "probability" not in json.dumps(alert).lower()


def test_non_finite_and_negative_levels_fail_closed() -> None:
    for field, value in (("last", float("nan")), ("support", float("inf")), ("resistance", float("-inf")), ("support", -1.0)):
        current = cockpit()
        current["levels"][field] = value
        with pytest.raises(ValueError, match="finite and strictly positive"):
            alerts.build(current)


def test_optional_unsafe_safety_fields_fail_closed() -> None:
    current = cockpit(); current["safety"]["uses_credentials"] = True
    with pytest.raises(ValueError, match="unsafe"):
        alerts.build(current)
    current = cockpit(); current["safety"]["read_only_analysis"] = False
    with pytest.raises(ValueError, match="unsafe"):
        alerts.build(current)
    current = cockpit(); current["safety"]["uses_credentials"] = False; current["safety"]["read_only_analysis"] = True
    assert alerts.build(current)["safety"]["can_trade"] is False


def test_status_stance_and_blockers_are_normalized_or_rejected() -> None:
    current = cockpit(); current["status"] = " READY "; current["executive"]["stance"] = " WATCH_LONG "
    alert = alerts.build(current)
    assert alert["decision"] == "NOTIFY"
    assert alert["level_state"] == "LONG_TRIGGER_ZONE"
    assert not any(item["kind"] == "STATUS_BLOCKED" for item in alert["events"])

    for bad in ("", "   "):
        current = cockpit(); current["quality"]["blockers"] = [bad]
        with pytest.raises(ValueError, match="non-empty strings"):
            alerts.build(current)


def test_semantically_equal_offset_times_are_not_ordered_by_text() -> None:
    current = cockpit(); current["as_of"] = "2026-08-09T23:01:36+07:00"
    previous = earlier(current); previous["as_of"] = "2026-08-09T16:01:36Z"
    with pytest.raises(ValueError, match="strictly earlier"):
        alerts.build(current, previous)


def test_zero_and_inverted_levels_fail_closed() -> None:
    for field in ("last", "support", "resistance"):
        current = cockpit(); current["levels"][field] = 0.0
        with pytest.raises(ValueError, match="strictly positive"):
            alerts.build(current)

    current = cockpit(); current["levels"]["support"] = 70000.0; current["levels"]["resistance"] = 60000.0
    with pytest.raises(ValueError, match="support must be lower"):
        alerts.build(current)

    current = cockpit(); current["levels"]["support"] = current["levels"]["resistance"]
    with pytest.raises(ValueError, match="support must be lower"):
        alerts.build(current)

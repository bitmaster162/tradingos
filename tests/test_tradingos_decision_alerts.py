from __future__ import annotations
import importlib.util, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "tools" / "tradingos_decision_alerts.py"
s = importlib.util.spec_from_file_location("alerts", P); assert s and s.loader
alerts = importlib.util.module_from_spec(s); s.loader.exec_module(alerts)


def cockpit() -> dict:
    return {
        "brief_id": "b1", "symbol": "BTCUSDT", "as_of": "2026-08-09T16:01:36Z", "status": "READY",
        "executive": {"stance": "WATCH_LONG", "next": "wait"},
        "levels": {"last": 65207.7, "support": 64111.0, "resistance": 65358.0},
        "risk_flags": [{"severity": "WATCH", "label": "Relative basis extreme", "detail": "z=1.88"}],
        "quality": {"blockers": []},
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def test_first_state_near_long_trigger_notifies() -> None:
    a = alerts.build(cockpit())
    assert a["decision"] == "NOTIFY"
    assert a["priority"] == "HIGH"
    assert a["level_state"] == "LONG_TRIGGER_ZONE"
    assert any(x["kind"] == "LEVEL_PROXIMITY" for x in a["events"])


def test_identical_state_is_silent() -> None:
    c = cockpit()
    a = alerts.build(c, json.loads(json.dumps(c)))
    assert a["decision"] == "SILENT"
    assert any(x["kind"] == "NO_MATERIAL_CHANGE" for x in a["events"])


def test_stance_change_notifies() -> None:
    cur, prev = cockpit(), cockpit()
    prev["executive"]["stance"] = "NO_ACTION"
    a = alerts.build(cur, prev)
    assert a["decision"] == "NOTIFY"
    assert any(x["kind"] == "STANCE_CHANGE" for x in a["events"])


def test_unsafe_cockpit_fails_closed() -> None:
    c = cockpit(); c["safety"]["orders_allowed"] = True
    try:
        alerts.build(c)
    except ValueError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("unsafe cockpit accepted")

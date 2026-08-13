from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("memory", ROOT / "tools" / "tradingos_market_memory.py")
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def radar(ts: str, *, btc_bias: str = "WATCH_LONG", priority: float = 80.0, vetoes=None, top="BTCUSDT"):
    vetoes = vetoes or []
    return {
        "schema": "tradingos.market_radar.v1",
        "watchtower_captured_at": ts,
        "liquidity_captured_at": ts,
        "top_priority": top,
        "matrix": [
            {
                "symbol": "BTCUSDT", "bias": btc_bias, "decision_quality": "CAUTION" if vetoes else "CLEAR",
                "priority_score": priority, "timeframes": {"1h": "LONG", "4h": "LONG", "1d": "LONG"},
                "confluence": 6, "watchtower_conflict": False,
                "liquidity": {"quality": "PASS", "state": "BALANCED", "spread_bps": 0.02},
                "vetoes": vetoes, "notes": [], "can_trade": False,
            },
            {
                "symbol": "ETHUSDT", "bias": "NO_ACTION", "decision_quality": "BLOCKED_BY_CONFLUENCE",
                "priority_score": 42.0, "timeframes": {"1h": "LONG", "4h": "LONG", "1d": "SHORT"},
                "confluence": 0, "watchtower_conflict": True,
                "liquidity": {"quality": "PARTIAL", "state": "INSUFFICIENT_DEPTH_COVERAGE", "spread_bps": 0.05},
                "vetoes": [], "notes": ["LIQUIDITY_CONTEXT_PARTIAL"], "can_trade": False,
            },
        ],
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def write(path: Path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_append_builds_verified_hash_chain_and_duplicate_is_suppressed(tmp_path: Path):
    ledger = tmp_path / "memory.ndjson"
    status, first, records = m.append_observation(ledger, radar("2026-08-09T12:00:00Z"))
    assert status == "APPENDED"
    assert first["sequence"] == 1 and first["prev_record_hash"] == "GENESIS"
    assert len(m.verify_ledger(ledger)) == 1
    status2, second, records2 = m.append_observation(ledger, radar("2026-08-09T12:00:00Z"))
    assert status2 == "DUPLICATE_SUPPRESSED"
    assert second["record_hash"] == first["record_hash"]
    assert len(records2) == 1


def test_material_transition_records_bias_priority_and_veto_changes(tmp_path: Path):
    ledger = tmp_path / "memory.ndjson"
    m.append_observation(ledger, radar("2026-08-09T12:00:00Z"))
    _, second, _ = m.append_observation(ledger, radar("2026-08-09T13:05:00Z", btc_bias="NO_ACTION", priority=63.0, vetoes=["NEAR_ASK_WALL_FRICTION"]))
    delta = second["change_from_previous"]
    assert delta["material_change"] is True
    fields = {(x["scope"], x["field"]) for x in delta["changes"]}
    assert ("BTCUSDT", "bias") in fields
    assert ("BTCUSDT", "priority_score") in fields
    assert ("BTCUSDT", "vetoes") in fields


def test_replay_uses_real_baselines_and_never_fabricates_missing_windows(tmp_path: Path):
    ledger = tmp_path / "memory.ndjson"
    m.append_observation(ledger, radar("2026-08-09T10:00:00Z"))
    _, _, records = m.append_observation(ledger, radar("2026-08-09T14:15:00Z", priority=70.0))
    replay = m.build_replay(records)
    assert replay["windows"]["1h"]["status"] == "COMPARABLE"
    assert replay["windows"]["4h"]["status"] == "COMPARABLE"
    assert replay["windows"]["24h"]["status"] == "INSUFFICIENT_HISTORY"
    assert replay["windows"]["24h"]["delta"] is None


def test_tampered_ledger_fails_closed(tmp_path: Path):
    ledger = tmp_path / "memory.ndjson"
    m.append_observation(ledger, radar("2026-08-09T12:00:00Z"))
    row = json.loads(ledger.read_text())
    row["state"]["symbols"]["BTCUSDT"]["bias"] = "WATCH_SHORT"
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")
    try:
        m.verify_ledger(ledger)
    except ValueError as exc:
        assert "record_hash mismatch" in str(exc) or "state_fingerprint mismatch" in str(exc)
    else:
        raise AssertionError("tampered ledger must fail closed")


def test_historical_backfill_is_rejected(tmp_path: Path):
    ledger = tmp_path / "memory.ndjson"
    m.append_observation(ledger, radar("2026-08-09T12:00:00Z"))
    try:
        m.append_observation(ledger, radar("2026-08-09T11:59:00Z"))
    except ValueError as exc:
        assert "historical backfill is disabled" in str(exc)
    else:
        raise AssertionError("backfill must be rejected")


def test_generate_outputs_replay_html_and_preserves_safety(tmp_path: Path):
    radar_path = tmp_path / "radar.json"
    ledger = tmp_path / "memory.ndjson"
    out = tmp_path / "out"
    write(radar_path, radar("2026-08-09T12:00:00Z"))
    status, paths, replay = m.generate(ledger, out, radar_path=radar_path)
    assert status == "APPENDED"
    assert paths["html"].exists() and paths["replay"].exists()
    assert replay["safety"]["can_trade"] is False
    assert replay["windows"]["1h"]["status"] == "INSUFFICIENT_HISTORY"
    assert "No historical state is fabricated" in paths["html"].read_text(encoding="utf-8")


def test_cockpit_alert_only_can_establish_real_baseline(tmp_path: Path):
    cockpit = {
        "schema": "tradingos.decision_cockpit.v1", "symbol": "BTCUSDT", "as_of": "2026-08-09T16:01:36Z", "status": "READY",
        "executive": {"stance": "WATCH_LONG", "regime": "TREND_UP", "grade": "STRONG", "margin": 4.5, "next": "wait"},
        "levels": {"last": 65207.7, "support": 64111.0, "resistance": 65358.0, "to_resistance_pct": 0.23},
        "risk_flags": [],
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }
    alert = {
        "schema": "tradingos.decision_alert.v1", "symbol": "BTCUSDT", "as_of": "2026-08-09T16:01:36Z",
        "decision": "NOTIFY", "priority": "HIGH", "level_state": "LONG_TRIGGER_ZONE", "dedupe_key": "abc",
        "events": [{"kind": "LEVEL_PROXIMITY"}],
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }
    status, rec, records = m.append_observation(tmp_path / "memory.ndjson", cockpit=cockpit, alert=alert)
    assert status == "APPENDED" and rec["observed_at"] == "2026-08-09T16:01:36Z"
    assert rec["state"]["symbols"] == {}
    assert rec["state"]["cockpit"]["stance"] == "WATCH_LONG"
    assert rec["state"]["alert"]["decision"] == "NOTIFY"

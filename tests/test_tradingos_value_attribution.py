from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


attr = load("attr", TOOLS / "tradingos_value_attribution.py")
memory = load("memory_exact", TOOLS / "tradingos_market_memory.py")
alerts = load("alerts_exact", TOOLS / "tradingos_decision_alerts.py")
state = load("state_exact", TOOLS / "tradingos_market_memory_state.py")


def cockpit(
    ts: str = "2026-08-09T16:00:00Z",
    *,
    brief_id: str = "b1",
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
    stance: str = "WATCH_LONG",
    status: str = "READY",
    last: float = 99.8,
    support: float = 95.0,
    resistance: float = 100.0,
    pressures: list[dict] | None = None,
    risk_labels: list[str] | None = None,
    blockers: list[str] | None = None,
) -> dict:
    if pressures is None:
        pressures = [
            {"label": "Price/OI alignment", "direction": "LONG", "strength": 2.0, "observation": "aligned"},
            {"label": "Spot CVD", "direction": "LONG", "strength": 1.0, "observation": "positive"},
        ]
    return {
        "schema": "tradingos.decision_cockpit.v1",
        "version": "1.3.0",
        "brief_id": brief_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": ts,
        "status": status,
        "executive": {
            "stance": stance,
            "regime": "TREND_UP",
            "grade": "STRONG",
            "margin": 4.5,
            "next": "Wait for confirmation; do not place an order from this brief.",
        },
        "pressure": pressures,
        "levels": {
            "last": last,
            "support": support,
            "resistance": resistance,
            "to_resistance_pct": round((resistance / last - 1) * 100, 3) if last else None,
        },
        "risk_flags": [
            {"severity": "WATCH", "label": label, "detail": label}
            for label in (risk_labels or [])
        ],
        "quality": {"blockers": blockers or []},
        "safety": {
            "signals": False,
            "orders": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }


def alert_for(current: dict, previous: dict | None = None) -> dict:
    return alerts.build(current, previous)


def accept(memory_ledger: Path, c: dict, a: dict) -> dict:
    status, row, _ = memory.append_observation(memory_ledger, c, a)
    assert status == "APPENDED"
    return row


def process_obs(tmp_path: Path, c: dict, a: dict, *, memory_ledger: Path | None = None, attr_ledger: Path | None = None):
    memory_ledger = memory_ledger or (tmp_path / "memory.ndjson")
    attr_ledger = attr_ledger or (tmp_path / "attr.ndjson")
    accept(memory_ledger, c, a)
    return attr.process(attr_ledger, memory_ledger, c, a)


def _rewrite_rows(ledger: Path, mutate) -> list[dict]:
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    mutate(rows)
    prev = attr.GENESIS
    for idx, row in enumerate(rows):
        row["sequence"] = idx + 1
        row["prev_record_hash"] = prev
        body = dict(row)
        body.pop("record_hash", None)
        row["record_hash"] = attr.sha(body)
        prev = row["record_hash"]
    ledger.write_text("\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")
    return rows


def _first_open(tmp_path: Path):
    c0 = cockpit()
    a0 = alert_for(c0)
    status, report, rows = process_obs(tmp_path, c0, a0)
    assert status == "APPENDED"
    return c0, a0, report, rows


def test_01_import_uses_current_canonical_public_api() -> None:
    assert hasattr(state, "validate_cockpit")
    assert hasattr(state, "validate_alert")
    assert not hasattr(state, "safe")
    assert attr.memory_tool.verify_ledger


def test_02_exact_canonical_memory_bound_happy_path(tmp_path: Path) -> None:
    c = cockpit(); a = alert_for(c)
    status, report, rows = process_obs(tmp_path, c, a)
    assert status == "APPENDED"
    assert report["summary"]["events"] == 1
    assert rows[0]["source_identity"] == state.source_identity(c, a)[0]
    assert rows[0]["source_memory_record_hash"] == rows[0]["observation_id"]


def test_03_missing_memory_record_rejected(tmp_path: Path) -> None:
    c = cockpit(); a = alert_for(c)
    with pytest.raises(ValueError, match="no accepted observation"):
        attr.process(tmp_path / "attr.ndjson", tmp_path / "missing.ndjson", c, a)


def test_04_memory_tail_cockpit_fingerprint_mismatch_rejected(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; c = cockpit(); a = alert_for(c); accept(ml, c, a)
    c2 = json.loads(json.dumps(c)); c2["story"] = ["mutated raw packet"]
    a2 = alert_for(c2)
    with pytest.raises(ValueError, match="source_identity"):
        attr.process(tmp_path / "attr.ndjson", ml, c2, a2)


def test_05_memory_tail_alert_fingerprint_mismatch_rejected(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; c = cockpit(); a = alert_for(c); accept(ml, c, a)
    a2 = json.loads(json.dumps(a)); a2["next_action"] = "changed but safe"
    with pytest.raises(ValueError, match="source_identity|next_action"):
        attr.process(tmp_path / "attr.ndjson", ml, c, a2)


@pytest.mark.parametrize("field,value", [("symbol", "ETHUSDT"), ("timeframe", "1h"), ("as_of", "2026-08-09T16:00:01Z")])
def test_06_08_packet_identity_mismatch_rejected(tmp_path: Path, field: str, value: str) -> None:
    c = cockpit(); a = alert_for(c); a[field] = value
    with pytest.raises(ValueError, match="identity mismatch"):
        attr._packet_context(c, a)


def test_09_stale_donor_cockpit_without_timeframe_rejected() -> None:
    c = cockpit(); c.pop("timeframe"); a = alert_for(cockpit())
    with pytest.raises(ValueError, match="timeframe"):
        attr._packet_context(c, a)


def test_10_stale_short_dedupe_key_rejected() -> None:
    c = cockpit(); a = alert_for(c); a["dedupe_key"] = "abc"
    with pytest.raises(ValueError, match="dedupe_key"):
        attr._packet_context(c, a)


@pytest.mark.parametrize("bad", [None, {}, "x"])
def test_11_13_malformed_pressure_container_rejected(bad) -> None:
    c = cockpit(); c["pressure"] = bad; a = alert_for(c)
    with pytest.raises(ValueError, match="pressure"):
        attr._packet_context(c, a)


def test_14_malformed_pressure_entry_rejected() -> None:
    c = cockpit(pressures=["bad"]); a = alert_for(c)
    with pytest.raises(ValueError, match=r"pressure\[0\]"):
        attr._packet_context(c, a)


def test_15_nonfinite_pressure_strength_rejected() -> None:
    c = cockpit(pressures=[{"label": "Spot CVD", "direction": "LONG", "strength": float("nan"), "observation": "x"}]); a = alert_for(c)
    with pytest.raises(ValueError, match="finite"):
        attr._packet_context(c, a)


def test_16_baseline_event_open(tmp_path: Path) -> None:
    _, _, report, rows = _first_open(tmp_path)
    assert rows[0]["record_type"] == "EVENT_OPEN"
    assert rows[0]["initial_outcome"] == "UNRESOLVED"
    assert report["events"][0]["timeframe"] == "4h"


def test_17_exact_duplicate_observation_suppressed(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"; c = cockpit(); a = alert_for(c); accept(ml, c, a)
    s1, _, _ = attr.process(al, ml, c, a)
    s2, _, rows = attr.process(al, ml, c, a)
    assert s1 == "APPENDED" and s2 == "DUPLICATE_OBSERVATION_SUPPRESSED"
    assert len(rows) == 1


def test_18_same_observation_conflicting_packet_rejected(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"; c = cockpit(); a = alert_for(c); accept(ml, c, a); attr.process(al, ml, c, a)
    a2 = json.loads(json.dumps(a)); a2["next_action"] = "other"
    with pytest.raises(ValueError, match="source_identity|next_action"):
        attr.process(al, ml, c, a2)


def test_19_historical_observation_replay_rejected(tmp_path: Path) -> None:
    al = tmp_path / "attr.ndjson"
    ml_new = tmp_path / "memory_new.ndjson"
    c0 = cockpit(); a0 = alert_for(c0); accept(ml_new, c0, a0); attr.process(al, ml_new, c0, a0)
    c1 = cockpit("2026-08-09T20:00:01Z", brief_id="b2", last=101.0); a1 = alert_for(c1, c0); accept(ml_new, c1, a1); attr.process(al, ml_new, c1, a1)
    ml_old = tmp_path / "memory_old.ndjson"; accept(ml_old, c0, a0)
    with pytest.raises(ValueError, match="historical attribution"):
        attr.process(al, ml_old, c0, a0)


def test_20_cross_stream_attribution_ledger_rejected(tmp_path: Path) -> None:
    al = tmp_path / "attr.ndjson"
    ml1 = tmp_path / "m1.ndjson"; c1 = cockpit(); a1 = alert_for(c1); accept(ml1, c1, a1); attr.process(al, ml1, c1, a1)
    ml2 = tmp_path / "m2.ndjson"; c2 = cockpit(symbol="ETHUSDT"); a2 = alert_for(c2); accept(ml2, c2, a2)
    with pytest.raises(ValueError, match="stream identity"):
        attr.process(al, ml2, c2, a2)


def test_21_long_trigger_confirms_after_4h_with_required_pressure(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    c0 = cockpit(); a0 = alert_for(c0); accept(ml, c0, a0); attr.process(al, ml, c0, a0)
    c1 = cockpit("2026-08-09T20:00:01Z", brief_id="b2", last=101.0); a1 = alert_for(c1, c0); accept(ml, c1, a1)
    _, report, _ = attr.process(al, ml, c1, a1)
    assert report["summary"]["confirmed"] == 1


def test_22_long_trigger_stays_unresolved_without_required_pressure(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    c0 = cockpit(); a0 = alert_for(c0); accept(ml, c0, a0); attr.process(al, ml, c0, a0)
    pressures = [{"label": "Price/OI alignment", "direction": "LONG", "strength": 1.0, "observation": "x"}]
    c1 = cockpit("2026-08-09T20:00:01Z", brief_id="b2", last=101.0, pressures=pressures); a1 = alert_for(c1, c0); accept(ml, c1, a1)
    status, report, rows = attr.process(al, ml, c1, a1)
    assert report["summary"]["unresolved"] == 1 and report["summary"]["confirmed"] == 0
    assert status in {"NO_ATTRIBUTABLE_EVENTS", "APPENDED"}


def test_23_long_trigger_invalidates_on_support_loss(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    c0 = cockpit(); a0 = alert_for(c0); accept(ml, c0, a0); attr.process(al, ml, c0, a0)
    c1 = cockpit("2026-08-09T20:01:00Z", brief_id="b2", stance="NO_ACTION", last=94.0); a1 = alert_for(c1, c0); accept(ml, c1, a1)
    _, report, _ = attr.process(al, ml, c1, a1)
    assert report["summary"]["invalidated"] == 1


def test_24_short_trigger_confirms_mirror(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    pressures0 = [
        {"label": "Price/OI alignment", "direction": "SHORT", "strength": 2.0, "observation": "aligned"},
        {"label": "Spot CVD", "direction": "SHORT", "strength": 1.0, "observation": "negative"},
    ]
    c0 = cockpit(stance="WATCH_SHORT", last=95.2, pressures=pressures0); a0 = alert_for(c0); accept(ml, c0, a0); attr.process(al, ml, c0, a0)
    c1 = cockpit("2026-08-09T20:00:01Z", brief_id="b2", stance="WATCH_SHORT", last=94.0, pressures=pressures0); a1 = alert_for(c1, c0); accept(ml, c1, a1)
    _, report, _ = attr.process(al, ml, c1, a1)
    assert report["summary"]["confirmed"] == 1


def test_25_stance_target_persistence_exactness(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    prev = cockpit("2026-08-09T12:00:00Z", stance="NO_ACTION", last=98.0, brief_id="b0")
    cur = cockpit("2026-08-09T16:00:00Z", stance="WATCH_LONG", brief_id="b1"); a = alert_for(cur, prev); accept(ml, cur, a); attr.process(al, ml, cur, a)
    later = cockpit("2026-08-09T20:00:01Z", stance="WATCH_LONG", last=99.0, brief_id="b2"); later_a = alert_for(later, cur); accept(ml, later, later_a)
    _, report, _ = attr.process(al, ml, later, later_a)
    assert report["summary"]["confirmed"] >= 1


def test_26_status_transition_to_ready_confirms_exact_ready_target(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    prev = cockpit("2026-08-09T14:00:00Z", status="BLOCKED", brief_id="b0", last=98.0)
    cur = cockpit("2026-08-09T16:00:00Z", status="READY", brief_id="b1", last=98.0); a = alert_for(cur, prev); accept(ml, cur, a); attr.process(al, ml, cur, a)
    later = cockpit("2026-08-09T17:01:00Z", status="READY", brief_id="b2", last=98.0); later_a = alert_for(later, cur); accept(ml, later, later_a)
    _, report, rows = attr.process(al, ml, later, later_a)
    assert any(r.get("outcome") == "CONFIRMED" for r in rows if r["record_type"] == "EVENT_RESOLUTION")
    open_row = next(r for r in rows if r["record_type"] == "EVENT_OPEN" and r["kind"] == "STATUS_CHANGE")
    assert open_row["resolution_contract"]["target_status"] == "READY"


def test_27_status_transition_away_from_ready_confirms_exact_target(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    prev = cockpit("2026-08-09T14:00:00Z", status="READY", brief_id="b0", last=98.0)
    cur = cockpit("2026-08-09T16:00:00Z", status="BLOCKED", brief_id="b1", last=98.0); a = alert_for(cur, prev); accept(ml, cur, a); attr.process(al, ml, cur, a)
    later = cockpit("2026-08-09T17:01:00Z", status="BLOCKED", brief_id="b2", last=98.0); later_a = alert_for(later, cur); accept(ml, later, later_a)
    _, _, rows = attr.process(al, ml, later, later_a)
    opens = [r for r in rows if r["record_type"] == "EVENT_OPEN" and r["kind"] in {"STATUS_BLOCKED", "STATUS_CHANGE"}]
    assert opens and all(r["resolution_contract"]["target_status"] == "BLOCKED" for r in opens)


def test_28_new_blocker_exact_label_persistence(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    prev = cockpit("2026-08-09T14:00:00Z", blockers=[], brief_id="b0", last=98.0)
    cur = cockpit("2026-08-09T16:00:00Z", blockers=["stale OI"], brief_id="b1", last=98.0); a = alert_for(cur, prev); accept(ml, cur, a); attr.process(al, ml, cur, a)
    later = cockpit("2026-08-09T17:01:00Z", blockers=["stale OI"], brief_id="b2", last=98.0); later_a = alert_for(later, cur); accept(ml, later, later_a)
    _, _, rows = attr.process(al, ml, later, later_a)
    row = next(r for r in rows if r["record_type"] == "EVENT_OPEN" and r["kind"] == "NEW_BLOCKER")
    assert row["resolution_contract"]["target_label"] == "stale OI"


def test_29_new_risk_exact_label_persistence(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    prev = cockpit("2026-08-09T14:00:00Z", risk_labels=[], brief_id="b0", last=98.0)
    cur = cockpit("2026-08-09T16:00:00Z", risk_labels=["basis extreme"], brief_id="b1", last=98.0); a = alert_for(cur, prev); accept(ml, cur, a); attr.process(al, ml, cur, a)
    later = cockpit("2026-08-09T17:01:00Z", risk_labels=["basis extreme"], brief_id="b2", last=98.0); later_a = alert_for(later, cur); accept(ml, later, later_a)
    _, _, rows = attr.process(al, ml, later, later_a)
    row = next(r for r in rows if r["record_type"] == "EVENT_OPEN" and r["kind"] == "NEW_RISK_FLAG")
    assert row["resolution_contract"]["target_label"] == "basis extreme"


def test_30_event_expires_after_24h(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    c0 = cockpit(); a0 = alert_for(c0); accept(ml, c0, a0); attr.process(al, ml, c0, a0)
    pressures = [{"label": "Price/OI alignment", "direction": "LONG", "strength": 1.0, "observation": "x"}]
    c1 = cockpit("2026-08-10T16:00:01Z", brief_id="b2", last=99.0, pressures=pressures); a1 = alert_for(c1, c0); accept(ml, c1, a1)
    _, report, _ = attr.process(al, ml, c1, a1)
    assert report["summary"]["expired"] == 1


def test_31_evaluate_same_or_earlier_observation_never_resolves(tmp_path: Path) -> None:
    _, _, _, rows = _first_open(tmp_path)
    open_row = rows[0]
    ctx = attr._packet_context(cockpit(), alert_for(cockpit()))
    ctx["source_memory_record_hash"] = "0" * 64
    assert attr._evaluate(open_row, ctx) is None


def test_32_record_hash_tamper_rejected(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger = tmp_path / "attr.ndjson"
    row = json.loads(ledger.read_text()); row["title"] = "tampered"; ledger.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="record_hash mismatch"):
        attr.verify_ledger(ledger)


def test_33_source_memory_hash_tamper_rejected_even_after_rehash(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger = tmp_path / "attr.ndjson"
    def mutate(rows): rows[0]["source_memory_record_hash"] = "0" * 64
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="observation/source memory hash mismatch|opening context memory hash mismatch"):
        attr.verify_ledger(ledger)


def test_34_stream_tamper_rejected_even_after_rehash(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger = tmp_path / "attr.ndjson"
    def mutate(rows): rows[0]["symbol"] = "ETHUSDT"
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="source_identity stream mismatch|opening context stream mismatch"):
        attr.verify_ledger(ledger)


def test_35_malformed_opening_context_rejected_even_after_rehash(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger = tmp_path / "attr.ndjson"
    def mutate(rows): rows[0]["opening_context"] = {}
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="opening_context"):
        attr.verify_ledger(ledger)


def test_36_malformed_resolution_contract_rejected_even_after_rehash(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger = tmp_path / "attr.ndjson"
    def mutate(rows): rows[0]["resolution_contract"] = {"type": "MAGIC", "minimum_evaluation_hours": 0, "expiry_hours": 1}
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="resolution_contract"):
        attr.verify_ledger(ledger)


def test_37_event_id_semantic_tamper_rejected_after_rehash(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger = tmp_path / "attr.ndjson"
    def mutate(rows): rows[0]["event_id"] = "a" * 24
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="event_id semantic mismatch"):
        attr.verify_ledger(ledger)


def test_38_duplicate_terminal_resolution_rejected(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    c0 = cockpit(); a0 = alert_for(c0); accept(ml, c0, a0); attr.process(al, ml, c0, a0)
    c1 = cockpit("2026-08-09T20:00:01Z", brief_id="b2", last=101.0); a1 = alert_for(c1, c0); accept(ml, c1, a1); attr.process(al, ml, c1, a1)
    rows = [json.loads(x) for x in al.read_text().splitlines()]
    resolution = next(r for r in rows if r["record_type"] == "EVENT_RESOLUTION")
    dup = dict(resolution); dup["sequence"] = len(rows) + 1; dup["prev_record_hash"] = rows[-1]["record_hash"]
    body = dict(dup); body.pop("record_hash", None); dup["record_hash"] = attr.sha(body)
    al.write_text(al.read_text() + json.dumps(dup, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(ValueError, match="duplicate terminal resolution"):
        attr.verify_ledger(al)


def test_39_blank_ledger_line_rejected(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger = tmp_path / "attr.ndjson"; ledger.write_bytes(ledger.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="blank record"):
        attr.verify_ledger(ledger)


def test_40_missing_final_newline_rejected(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger = tmp_path / "attr.ndjson"; ledger.write_bytes(ledger.read_bytes().rstrip(b"\n"))
    with pytest.raises(ValueError, match="end with a newline"):
        attr.verify_ledger(ledger)


def test_41_partial_os_write_completed(tmp_path: Path, monkeypatch) -> None:
    real_write = attr.os.write; calls = {"n": 0}
    def short_once(fd, data):
        calls["n"] += 1
        if calls["n"] == 1 and len(data) > 20:
            return real_write(fd, data[:20])
        return real_write(fd, data)
    monkeypatch.setattr(attr.os, "write", short_once)
    _first_open(tmp_path)
    assert calls["n"] >= 2 and len(attr.verify_ledger(tmp_path / "attr.ndjson")) == 1


def test_42_injected_write_failure_rolls_back_whole_multirow_transaction(tmp_path: Path, monkeypatch) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    c0 = cockpit(); a0 = alert_for(c0); accept(ml, c0, a0); attr.process(al, ml, c0, a0)
    before = al.read_bytes()
    c1 = cockpit("2026-08-09T20:00:01Z", brief_id="b2", last=101.0, risk_labels=["new risk"]); a1 = alert_for(c1, c0); accept(ml, c1, a1)
    real_write = attr.os.write; calls = {"n": 0}
    def fail_after_partial(fd, data):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_write(fd, data[: max(1, len(data)//3)])
        raise OSError("injected failure")
    monkeypatch.setattr(attr.os, "write", fail_after_partial)
    with pytest.raises(OSError, match="injected failure"):
        attr.process(al, ml, c1, a1)
    assert al.read_bytes() == before
    assert len(attr.verify_ledger(al)) == 1


def test_43_concurrent_identical_cli_writers_do_not_fork(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"; c = cockpit(); a = alert_for(c); accept(ml, c, a)
    cp = tmp_path / "c.json"; ap = tmp_path / "a.json"; cp.write_text(json.dumps(c)); ap.write_text(json.dumps(a))
    cmd = [sys.executable, str(TOOLS / "tradingos_value_attribution.py"), "--memory-ledger", str(ml), "--attribution-ledger", str(al), "--cockpit", str(cp), "--alert", str(ap)]
    p1 = subprocess.Popen(cmd + ["--out-dir", str(tmp_path / "o1")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p2 = subprocess.Popen(cmd + ["--out-dir", str(tmp_path / "o2")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    o1, e1 = p1.communicate(timeout=20); o2, e2 = p2.communicate(timeout=20)
    assert p1.returncode == p2.returncode == 0, (e1, e2)
    statuses = sorted([json.loads(o1)["process_status"], json.loads(o2)["process_status"]])
    assert statuses == ["APPENDED", "DUPLICATE_OBSERVATION_SUPPRESSED"]
    assert len(attr.verify_ledger(al)) == 1


def test_44_concurrent_different_next_observations_preserve_valid_ledger(tmp_path: Path) -> None:
    base_mem = tmp_path / "m0.ndjson"; al = tmp_path / "attr.ndjson"
    c0 = cockpit(); a0 = alert_for(c0); accept(base_mem, c0, a0); attr.process(al, base_mem, c0, a0)

    m1 = tmp_path / "m1.ndjson"; m1.write_bytes(base_mem.read_bytes())
    c1 = cockpit("2026-08-09T20:00:01Z", brief_id="b2", last=101.0); a1 = alert_for(c1, c0); accept(m1, c1, a1)
    m2 = tmp_path / "m2.ndjson"; m2.write_bytes(m1.read_bytes())
    c2 = cockpit("2026-08-09T21:00:01Z", brief_id="b3", last=101.5); a2 = alert_for(c2, c1); accept(m2, c2, a2)
    c1p=tmp_path/'c1.json'; a1p=tmp_path/'a1.json'; c2p=tmp_path/'c2.json'; a2p=tmp_path/'a2.json'
    for p,obj in [(c1p,c1),(a1p,a1),(c2p,c2),(a2p,a2)]: p.write_text(json.dumps(obj))
    def cmd(m,c,a,out):
        return [sys.executable, str(TOOLS/'tradingos_value_attribution.py'), '--memory-ledger', str(m), '--attribution-ledger', str(al), '--cockpit', str(c), '--alert', str(a), '--out-dir', str(out)]
    p1=subprocess.Popen(cmd(m1,c1p,a1p,tmp_path/'o1'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    p2=subprocess.Popen(cmd(m2,c2p,a2p,tmp_path/'o2'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    o1,e1=p1.communicate(timeout=20); o2,e2=p2.communicate(timeout=20)
    assert p1.returncode in {0,2} and p2.returncode in {0,2}, (o1,e1,o2,e2)
    rows=attr.verify_ledger(al)
    assert rows and all(r["symbol"]=="BTCUSDT" for r in rows)


def test_45_report_contains_timeframe_and_memory_provenance(tmp_path: Path) -> None:
    _, _, report, _ = _first_open(tmp_path)
    event = report["events"][0]
    assert event["timeframe"] == "4h"
    assert len(event["source_memory_record_hash"]) == 64


def test_46_report_forbids_pnl_and_execution_claims(tmp_path: Path) -> None:
    _, _, report, _ = _first_open(tmp_path)
    assert report["contract"]["pnl_attribution"] is False
    assert report["contract"]["execution_claims"] is False


def test_47_persisted_safety_is_exact(tmp_path: Path) -> None:
    _, _, report, rows = _first_open(tmp_path)
    assert report["safety"] == attr.SAFETY
    assert all(row["safety"] == attr.SAFETY for row in rows)


def test_48_event_id_is_deterministic_lowercase_hex(tmp_path: Path) -> None:
    _, _, _, rows = _first_open(tmp_path)
    eid = rows[0]["event_id"]
    assert len(eid) == 24 and all(ch in "0123456789abcdef" for ch in eid)


def test_49_no_attributable_event_observation_adds_no_rows(tmp_path: Path) -> None:
    ml = tmp_path / "memory.ndjson"; al = tmp_path / "attr.ndjson"
    c = cockpit(last=98.0, stance="NO_ACTION"); a = alert_for(c); accept(ml, c, a)
    status, report, rows = attr.process(al, ml, c, a)
    assert status == "NO_ATTRIBUTABLE_EVENTS" and rows == [] and report["summary"]["events"] == 0


def test_50_invalid_record_version_rejected(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows): rows[0]["version"]='0.0.0'
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='schema/version'): attr.verify_ledger(ledger)


def test_51_invalid_event_id_format_rejected(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows): rows[0]["event_id"]='abc'
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='event_id'): attr.verify_ledger(ledger)


def test_52_invalid_safety_rejected_after_rehash(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows): rows[0]["safety"]["can_trade"]=True
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='safety'): attr.verify_ledger(ledger)


def test_53_resolution_before_open_rejected(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    rows=[json.loads(x) for x in ledger.read_text().splitlines()]
    open_row=rows[0]
    fake={
        "schema":attr.SCHEMA,"version":attr.VERSION,"sequence":1,"recorded_at":"2026-08-09T20:00:01Z","prev_record_hash":attr.GENESIS,
        "record_type":"EVENT_RESOLUTION","observation_id":"1"*64,"source_memory_sequence":2,"source_memory_record_hash":"1"*64,
        "source_identity_fingerprint":open_row["source_identity_fingerprint"],"source_identity":open_row["source_identity"],"symbol":"BTCUSDT","timeframe":"4h",
        "event_id":open_row["event_id"],"opened_at":open_row["opened_at"],"evaluated_at":"2026-08-09T20:00:01Z","outcome":"CONFIRMED","resolution_hours":4.0003,
        "evidence":{},"safety":attr.SAFETY,
    }
    fake["source_identity"]=dict(fake["source_identity"]); fake["source_identity"]["as_of"]="2026-08-09T20:00:01Z"; fake["source_identity_fingerprint"]=attr.sha(fake["source_identity"])
    body=dict(fake); fake["record_hash"]=attr.sha(body)
    ledger.write_text(json.dumps(fake,sort_keys=True,separators=(",",":"))+"\n")
    with pytest.raises(ValueError,match='resolution before'): attr.verify_ledger(ledger)


def test_54_source_identity_tamper_rejected_after_rehash(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows): rows[0]["source_identity"]["brief_id"]='other'
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='source_identity_fingerprint mismatch'): attr.verify_ledger(ledger)


def test_55_source_memory_sequence_regression_rejected(tmp_path: Path) -> None:
    ml=tmp_path/'memory.ndjson'; al=tmp_path/'attr.ndjson'; c0=cockpit(); a0=alert_for(c0); accept(ml,c0,a0); attr.process(al,ml,c0,a0)
    c1=cockpit('2026-08-09T20:00:01Z',brief_id='b2',last=101); a1=alert_for(c1,c0); accept(ml,c1,a1); attr.process(al,ml,c1,a1)
    def mutate(rows):
        for r in rows:
            if r["observation_id"]!=rows[0]["observation_id"]: r["source_memory_sequence"]=1
    _rewrite_rows(al,mutate)
    with pytest.raises(ValueError,match='source_memory_sequence'): attr.verify_ledger(al)


def test_56_python_compile_passes() -> None:
    r=subprocess.run([sys.executable,'-m','py_compile',str(TOOLS/'tradingos_value_attribution.py')],capture_output=True,text=True)
    assert r.returncode==0, r.stderr


def test_57_production_imports_are_stdlib_plus_canonical_local() -> None:
    source=(TOOLS/'tradingos_value_attribution.py').read_text()
    forbidden=['requests','httpx','urllib','socket','telegram','webhook','ccxt']
    assert not any(f'import {x}' in source or f'from {x}' in source for x in forbidden)
    assert 'tradingos_market_memory' in source and 'tradingos_market_memory_state' in source


def test_58_no_deploy_runtime_or_network_tokens() -> None:
    source=(TOOLS/'tradingos_value_attribution.py').read_text().lower()
    for token in ['https://','http://','telegram','send_message','create_order','place_order','docker','deploy_permission']:
        assert token not in source

def test_59_extra_contract_field_rejected_after_rehash(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows): rows[0]['resolution_contract']['execution_allowed']=True
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='resolution_contract fields mismatch'): attr.verify_ledger(ledger)


def test_60_hidden_record_field_rejected_after_rehash(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows): rows[0]['capital_permission']='ALLOW'
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='record fields mismatch'): attr.verify_ledger(ledger)



def _accept_semantically_bad_memory_then_attribute(tmp_path: Path, mutate) -> None:
    ml = tmp_path / "memory.ndjson"
    al = tmp_path / "attr.ndjson"
    c = cockpit()
    a = alert_for(c)
    mutate(c, a)
    # Canonical Memory accepts structurally safe packets and fingerprints them;
    # Attribution must still reject internal Alert contradictions before attribution.
    accept(ml, c, a)
    attr.process(al, ml, c, a)


def test_61_silent_with_material_event_rejected_after_memory_acceptance(tmp_path: Path) -> None:
    def mutate(c, a): a["decision"] = "SILENT"
    with pytest.raises(ValueError, match="decision.*inconsistent"):
        _accept_semantically_bad_memory_then_attribute(tmp_path, mutate)


def test_62_unsupported_top_level_priority_rejected_after_memory_acceptance(tmp_path: Path) -> None:
    def mutate(c, a): a["priority"] = "BANANA"
    with pytest.raises(ValueError, match="priority is unsupported"):
        _accept_semantically_bad_memory_then_attribute(tmp_path, mutate)


def test_63_top_level_priority_must_match_event_maximum(tmp_path: Path) -> None:
    def mutate(c, a): a["priority"] = "INFO"
    with pytest.raises(ValueError, match="priority.*inconsistent"):
        _accept_semantically_bad_memory_then_attribute(tmp_path, mutate)


def test_64_level_state_must_match_current_cockpit(tmp_path: Path) -> None:
    def mutate(c, a): a["level_state"] = "MID_RANGE"
    with pytest.raises(ValueError, match="level_state does not match"):
        _accept_semantically_bad_memory_then_attribute(tmp_path, mutate)


def test_65_new_risk_event_must_exist_in_current_cockpit(tmp_path: Path) -> None:
    def mutate(c, a):
        a["events"] = [{"kind":"NEW_RISK_FLAG","priority":"MEDIUM","title":"New risk veto/flag","detail":"not-present"}]
        a["decision"] = "NOTIFY"; a["priority"] = "MEDIUM"
    with pytest.raises(ValueError, match="NEW_RISK_FLAG.*absent"):
        _accept_semantically_bad_memory_then_attribute(tmp_path, mutate)


def test_66_new_blocker_event_must_exist_in_current_cockpit(tmp_path: Path) -> None:
    def mutate(c, a):
        a["events"] = [{"kind":"NEW_BLOCKER","priority":"CRITICAL","title":"New data blocker","detail":"not-present"}]
        a["decision"] = "NOTIFY"; a["priority"] = "CRITICAL"
    with pytest.raises(ValueError, match="NEW_BLOCKER.*absent"):
        _accept_semantically_bad_memory_then_attribute(tmp_path, mutate)


def test_67_trigger_event_must_be_in_trigger_zone(tmp_path: Path) -> None:
    def mutate(c, a):
        # Make Cockpit MID_RANGE, then fabricate a LEVEL_PROXIMITY event that Memory still fingerprints.
        c["levels"]["last"] = 97.0
        a2 = alert_for(c)
        a.clear(); a.update(a2)
        a["events"] = [{"kind":"LEVEL_PROXIMITY","priority":"HIGH","title":"fake proximity","detail":"fake"}]
        a["decision"] = "NOTIFY"; a["priority"] = "HIGH"
    with pytest.raises(ValueError, match="outside a trigger zone"):
        _accept_semantically_bad_memory_then_attribute(tmp_path, mutate)


def test_68_valid_length_but_wrong_dedupe_key_rejected(tmp_path: Path) -> None:
    def mutate(c, a): a["dedupe_key"] = "0" * 24
    with pytest.raises(ValueError, match="dedupe_key does not match"):
        _accept_semantically_bad_memory_then_attribute(tmp_path, mutate)


def test_69_quiet_event_cannot_coexist_with_material_event(tmp_path: Path) -> None:
    def mutate(c, a):
        a["events"].append({"kind":"NO_MATERIAL_CHANGE","priority":"INFO","title":"No material decision change","detail":"unchanged"})
    with pytest.raises(ValueError, match="must be the only alert event"):
        _accept_semantically_bad_memory_then_attribute(tmp_path, mutate)


def test_70_noncanonical_decision_value_rejected(tmp_path: Path) -> None:
    def mutate(c, a): a["decision"] = "EXECUTE"
    with pytest.raises(ValueError, match="decision must be SILENT or NOTIFY"):
        _accept_semantically_bad_memory_then_attribute(tmp_path, mutate)


def test_71_rehashed_unsupported_open_kind_rejected(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows):
        r=rows[0]; r['kind']='BANANA'; r['resolution_contract']={'type':'OBSERVATION_ONLY','minimum_evaluation_hours':0.0,'expiry_hours':24.0}
        ctx=r['opening_context']; r['event_id']=attr.sha({'symbol':r['symbol'],'timeframe':r['timeframe'],'brief_id':ctx['brief_id'],'opened_at':r['opened_at'],'source_memory_record_hash':r['source_memory_record_hash'],'dedupe_key':r['dedupe_key'],'kind':r['kind'],'index':r['event_index']})[:attr.EVENT_ID_HEX]
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='unsupported attributable event kind'): attr.verify_ledger(ledger)


def test_72_rehashed_open_priority_must_match_kind(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows): rows[0]['priority']='BANANA'
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='event priority'): attr.verify_ledger(ledger)


def test_73_rehashed_new_risk_must_exist_in_opening_context(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows):
        r=rows[0]; r['kind']='NEW_RISK_FLAG'; r['priority']='MEDIUM'; r['detail']='ghost'; r['resolution_contract']={'type':'RISK_PERSISTENCE','target_label':'ghost','minimum_evaluation_hours':1.0,'expiry_hours':24.0}
        ctx=r['opening_context']; r['event_id']=attr.sha({'symbol':r['symbol'],'timeframe':r['timeframe'],'brief_id':ctx['brief_id'],'opened_at':r['opened_at'],'source_memory_record_hash':r['source_memory_record_hash'],'dedupe_key':r['dedupe_key'],'kind':r['kind'],'index':r['event_index']})[:attr.EVENT_ID_HEX]
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='NEW_RISK_FLAG.*absent'): attr.verify_ledger(ledger)


def test_74_rehashed_status_blocked_cannot_claim_ready(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows):
        r=rows[0]; r['kind']='STATUS_BLOCKED'; r['priority']='CRITICAL'; r['detail']='status=READY'; r['opening_context']['status']='READY'; r['resolution_contract']={'type':'STATUS_PERSISTENCE','target_status':'READY','minimum_evaluation_hours':1.0,'expiry_hours':24.0}
        ctx=r['opening_context']; r['event_id']=attr.sha({'symbol':r['symbol'],'timeframe':r['timeframe'],'brief_id':ctx['brief_id'],'opened_at':r['opened_at'],'source_memory_record_hash':r['source_memory_record_hash'],'dedupe_key':r['dedupe_key'],'kind':r['kind'],'index':r['event_index']})[:attr.EVENT_ID_HEX]
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='STATUS_BLOCKED'): attr.verify_ledger(ledger)


def test_75_rehashed_opening_level_state_must_match_levels_and_stance(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows):
        r=rows[0]; r['opening_context']['level_state']='MID_RANGE'; r['resolution_contract']={'type':'OBSERVATION_ONLY','minimum_evaluation_hours':0.0,'expiry_hours':24.0}
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='level_state does not match'): attr.verify_ledger(ledger)


def test_76_rehashed_dedupe_must_match_opening_context(tmp_path: Path) -> None:
    _first_open(tmp_path); ledger=tmp_path/'attr.ndjson'
    def mutate(rows):
        r=rows[0]; r['dedupe_key']='0'*24
        ctx=r['opening_context']; r['event_id']=attr.sha({'symbol':r['symbol'],'timeframe':r['timeframe'],'brief_id':ctx['brief_id'],'opened_at':r['opened_at'],'source_memory_record_hash':r['source_memory_record_hash'],'dedupe_key':r['dedupe_key'],'kind':r['kind'],'index':r['event_index']})[:attr.EVENT_ID_HEX]
    _rewrite_rows(ledger,mutate)
    with pytest.raises(ValueError,match='dedupe_key does not match'): attr.verify_ledger(ledger)

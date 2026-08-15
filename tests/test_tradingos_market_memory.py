from __future__ import annotations

import importlib.util
import json
import subprocess
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("memory", TOOLS / "tradingos_market_memory.py")
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def cockpit(ts: str = "2026-08-09T16:01:36Z", *, brief_id: str = "b1", stance: str = "WATCH_LONG") -> dict:
    return {
        "schema": "tradingos.decision_cockpit.v1",
        "version": "1.3.0",
        "brief_id": brief_id,
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "as_of": ts,
        "status": "READY",
        "executive": {
            "stance": stance,
            "regime": "TREND_UP",
            "grade": "STRONG",
            "margin": 4.5,
            "next": "Wait for confirmation; do not place an order from this brief.",
        },
        "levels": {
            "last": 65207.7,
            "support": 64111.0,
            "resistance": 65358.0,
            "to_resistance_pct": 0.23,
        },
        "risk_flags": [{"severity": "WATCH", "label": "Relative basis extreme", "detail": "z=1.88"}],
        "quality": {"blockers": []},
        "safety": {
            "signals": False,
            "orders": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }


def alert(c: dict, *, decision: str = "NOTIFY", dedupe: str = "a" * 24) -> dict:
    return {
        "schema": "tradingos.decision_alert.v1",
        "version": "1.2.1",
        "brief_id": c["brief_id"],
        "symbol": c["symbol"],
        "timeframe": c["timeframe"],
        "as_of": c["as_of"],
        "decision": decision,
        "priority": "HIGH" if decision == "NOTIFY" else "INFO",
        "level_state": "LONG_TRIGGER_ZONE",
        "events": [{"kind": "LEVEL_PROXIMITY", "priority": "HIGH", "title": "x", "detail": "y"}],
        "dedupe_key": dedupe,
        "next_action": "wait",
        "safety": {
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }


def _rewrite_record(ledger: Path, mutate) -> None:
    row = json.loads(ledger.read_text(encoding="utf-8"))
    mutate(row)
    body = dict(row)
    body.pop("record_hash", None)
    row["record_hash"] = m.sha(body)
    ledger.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")



def test_01_baseline_append_builds_verified_hash_chain(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c = cockpit()
    status, first, records = m.append_observation(ledger, c, alert(c))
    assert status == "APPENDED"
    assert first["sequence"] == 1
    assert first["prev_record_hash"] == "GENESIS"
    assert first["source_identity"]["brief_id"] == "b1"
    assert len(records) == len(m.verify_ledger(ledger)) == 1


def test_02_exact_duplicate_is_suppressed(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c = cockpit(); a = alert(c)
    _, first, _ = m.append_observation(ledger, c, a)
    status, second, records = m.append_observation(ledger, c, a)
    assert status == "DUPLICATE_SUPPRESSED"
    assert second["record_hash"] == first["record_hash"]
    assert len(records) == 1


def test_03_same_time_different_source_identity_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c1 = cockpit(); m.append_observation(ledger, c1, alert(c1))
    c2 = cockpit(brief_id="b2")
    with pytest.raises(ValueError, match="conflicting observation"):
        m.append_observation(ledger, c2, alert(c2))


def test_04_same_time_different_semantic_state_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c1 = cockpit(); m.append_observation(ledger, c1, alert(c1))
    c2 = cockpit(stance="NO_ACTION")
    with pytest.raises(ValueError, match="conflicting observation"):
        m.append_observation(ledger, c2, alert(c2))


def test_05_historical_backfill_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c1 = cockpit("2026-08-09T16:01:36Z"); m.append_observation(ledger, c1)
    c0 = cockpit("2026-08-09T15:01:36Z", brief_id="b0")
    with pytest.raises(ValueError, match="historical backfill"):
        m.append_observation(ledger, c0)


def test_06_record_hash_tamper_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"; c = cockpit(); m.append_observation(ledger, c)
    row = json.loads(ledger.read_text()); row["state"]["cockpit"]["stance"] = "WATCH_SHORT"
    ledger.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="record_hash mismatch"):
        m.verify_ledger(ledger)


def test_07_state_fingerprint_tamper_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"; c = cockpit(); m.append_observation(ledger, c)
    _rewrite_record(ledger, lambda row: row.__setitem__("state_fingerprint", "0" * 64))
    with pytest.raises(ValueError, match="state_fingerprint mismatch"):
        m.verify_ledger(ledger)


def test_08_source_identity_fingerprint_tamper_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"; c = cockpit(); m.append_observation(ledger, c)
    _rewrite_record(ledger, lambda row: row.__setitem__("source_identity_fingerprint", "0" * 64))
    with pytest.raises(ValueError, match="source_identity_fingerprint mismatch"):
        m.verify_ledger(ledger)


def test_09_non_contiguous_sequence_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"; c = cockpit(); m.append_observation(ledger, c)
    _rewrite_record(ledger, lambda row: row.__setitem__("sequence", 2))
    with pytest.raises(ValueError, match="non-contiguous"):
        m.verify_ledger(ledger)


def test_10_blank_ledger_line_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"; c = cockpit(); m.append_observation(ledger, c)
    ledger.write_bytes(ledger.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="blank record"):
        m.verify_ledger(ledger)


def test_11_missing_final_newline_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"; c = cockpit(); m.append_observation(ledger, c)
    ledger.write_bytes(ledger.read_bytes().rstrip(b"\n"))
    with pytest.raises(ValueError, match="end with a newline"):
        m.verify_ledger(ledger)


@pytest.mark.parametrize("field,value", [("symbol", "ETHUSDT"), ("timeframe", "1h"), ("brief_id", "other"), ("as_of", "2026-08-09T16:01:37Z")])
def test_12_15_cockpit_alert_identity_mismatch_rejected(tmp_path: Path, field: str, value: str) -> None:
    c = cockpit(); a = alert(c); a[field] = value
    with pytest.raises(ValueError, match=f"identity mismatch: {field}"):
        m.append_observation(tmp_path / "memory.ndjson", c, a)


def test_16_wrong_cockpit_schema_rejected(tmp_path: Path) -> None:
    c = cockpit(); c["schema"] = "other"
    with pytest.raises(ValueError, match="cockpit schema"):
        m.append_observation(tmp_path / "memory.ndjson", c)


def test_17_wrong_alert_schema_rejected(tmp_path: Path) -> None:
    c = cockpit(); a = alert(c); a["schema"] = "other"
    with pytest.raises(ValueError, match="alert schema"):
        m.append_observation(tmp_path / "memory.ndjson", c, a)


@pytest.mark.parametrize("packet_kind,field,value", [
    ("cockpit", "can_trade", True),
    ("cockpit", "signals_allowed", True),
    ("cockpit", "orders_allowed", True),
    ("alert", "can_trade", True),
])
def test_18_unsafe_permission_rejected(tmp_path: Path, packet_kind: str, field: str, value) -> None:
    c = cockpit(); a = alert(c)
    target = c if packet_kind == "cockpit" else a
    target["safety"][field] = value
    with pytest.raises(ValueError, match="unsafe permission"):
        m.append_observation(tmp_path / "memory.ndjson", c, a if packet_kind == "alert" else None)


def test_19_optional_unsafe_flags_rejected(tmp_path: Path) -> None:
    c = cockpit(); c["safety"]["uses_credentials"] = True
    with pytest.raises(ValueError, match="unsafe permission"):
        m.append_observation(tmp_path / "memory.ndjson", c)
    c = cockpit(); c["safety"]["read_only_analysis"] = False
    with pytest.raises(ValueError, match="unsafe permission"):
        m.append_observation(tmp_path / "memory.ndjson", c)


@pytest.mark.parametrize("mutator,match", [
    (lambda c: c.__setitem__("executive", []), "executive"),
    (lambda c: c.__setitem__("levels", []), "levels"),
    (lambda c: c.__setitem__("risk_flags", {}), "risk_flags"),
    (lambda c: c.__setitem__("quality", []), "quality"),
    (lambda c: c["quality"].__setitem__("blockers", [""]), "blockers"),
])
def test_20_malformed_cockpit_structures_rejected(tmp_path: Path, mutator, match: str) -> None:
    c = cockpit(); mutator(c)
    with pytest.raises(ValueError, match=match):
        m.append_observation(tmp_path / "memory.ndjson", c)


@pytest.mark.parametrize("mutator,match", [
    (lambda a: a.__setitem__("events", {}), "events"),
    (lambda a: a.__setitem__("events", [{}]), "kind"),
    (lambda a: a.__setitem__("dedupe_key", ""), "dedupe_key"),
    (lambda a: a.__setitem__("decision", ""), "decision"),
])
def test_21_malformed_alert_structures_rejected(tmp_path: Path, mutator, match: str) -> None:
    c = cockpit(); a = alert(c); mutator(a)
    with pytest.raises(ValueError, match=match):
        m.append_observation(tmp_path / "memory.ndjson", c, a)


def test_22_replay_uses_real_baselines_and_no_fabrication(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c1 = cockpit("2026-08-09T10:00:00Z", brief_id="b1"); m.append_observation(ledger, c1)
    c2 = cockpit("2026-08-09T14:15:00Z", brief_id="b2", stance="NO_ACTION")
    _, _, records = m.append_observation(ledger, c2)
    replay = m.build_replay(records)
    assert replay["windows"]["1h"]["status"] == "COMPARABLE"
    assert replay["windows"]["4h"]["status"] == "COMPARABLE"
    assert replay["windows"]["24h"]["status"] == "INSUFFICIENT_HISTORY"
    assert replay["windows"]["24h"]["delta"] is None


def test_23_cockpit_only_canonical_shape_compatible(tmp_path: Path) -> None:
    c = cockpit()
    status, record, _ = m.append_observation(tmp_path / "memory.ndjson", c)
    assert status == "APPENDED"
    assert record["source_identity"]["alert_fingerprint"] is None
    assert record["state"]["cockpit"]["timeframe"] == "4h"
    assert record["safety"] == m.SAFETY


def test_24_cockpit_plus_canonical_alert_shape_compatible(tmp_path: Path) -> None:
    c = cockpit(); a = alert(c)
    status, record, _ = m.append_observation(tmp_path / "memory.ndjson", c, a)
    assert status == "APPENDED"
    assert record["state"]["alert"]["decision"] == "NOTIFY"
    assert record["state"]["alert"]["dedupe_key"] == "a" * 24


def test_25_persisted_safety_and_replay_contract(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"; c = cockpit(); _, rec, records = m.append_observation(ledger, c)
    replay = m.build_replay(records)
    assert rec["safety"] == m.SAFETY
    assert replay["safety"] == m.SAFETY
    assert replay["contract"]["exclusive_writer_lock"] is True
    assert replay["contract"]["fsync_before_success"] is True


def test_26_source_identity_separate_from_semantic_state(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c1 = cockpit("2026-08-09T16:00:00Z", brief_id="b1")
    _, first, _ = m.append_observation(ledger, c1)
    c2 = cockpit("2026-08-09T17:00:00Z", brief_id="b2")
    _, second, _ = m.append_observation(ledger, c2)
    assert first["state_fingerprint"] == second["state_fingerprint"]
    assert first["source_identity_fingerprint"] != second["source_identity_fingerprint"]
    assert second["change_from_previous"]["summary"] == "NO_MATERIAL_CHANGE"


def test_27_concurrent_identical_writers_cannot_fork_chain(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c = cockpit(); a = alert(c)
    cp = tmp_path / "cockpit.json"; ap = tmp_path / "alert.json"
    cp.write_text(json.dumps(c)); ap.write_text(json.dumps(a))
    command = [sys.executable, str(TOOLS / "tradingos_market_memory.py"), "--cockpit", str(cp), "--alert", str(ap), "--ledger", str(ledger)]
    p1 = subprocess.Popen(command + ["--out-dir", str(tmp_path / "out1")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p2 = subprocess.Popen(command + ["--out-dir", str(tmp_path / "out2")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    o1, e1 = p1.communicate(timeout=15); o2, e2 = p2.communicate(timeout=15)
    assert p1.returncode == p2.returncode == 0, (e1, e2)
    r1, r2 = json.loads(o1), json.loads(o2)
    assert sorted([r1["append_status"], r2["append_status"]]) == ["APPENDED", "DUPLICATE_SUPPRESSED"]
    records = m.verify_ledger(ledger)
    assert len(records) == 1 and records[0]["sequence"] == 1


def test_28_generate_outputs_replay_html_and_preserves_safety(tmp_path: Path) -> None:
    c = cockpit(); a = alert(c)
    cp = tmp_path / "cockpit.json"; ap = tmp_path / "alert.json"
    cp.write_text(json.dumps(c)); ap.write_text(json.dumps(a))
    status, paths, replay = m.generate(tmp_path / "memory.ndjson", tmp_path / "out", cp, ap)
    assert status == "APPENDED"
    assert all(path.exists() for path in paths.values())
    assert replay["safety"]["can_trade"] is False
    assert "locked writer" in paths["html"].read_text(encoding="utf-8")


def test_29_wrong_record_version_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"; c = cockpit(); m.append_observation(ledger, c)
    _rewrite_record(ledger, lambda row: row.__setitem__("version", "0.0.0"))
    with pytest.raises(ValueError, match="invalid record"):
        m.verify_ledger(ledger)


def test_30_source_identity_timestamp_must_match_record(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"; c = cockpit(); m.append_observation(ledger, c)
    def mutate(row):
        row["source_identity"]["as_of"] = "2026-08-09T16:01:37Z"
        row["source_identity_fingerprint"] = m.sha(row["source_identity"])
    _rewrite_record(ledger, mutate)
    with pytest.raises(ValueError, match="source identity timestamp mismatch"):
        m.verify_ledger(ledger)


def test_31_ledger_stream_symbol_timeframe_is_bound(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c1 = cockpit("2026-08-09T16:00:00Z", brief_id="b1"); m.append_observation(ledger, c1)
    c2 = cockpit("2026-08-09T17:00:00Z", brief_id="b2"); c2["symbol"] = "ETHUSDT"
    with pytest.raises(ValueError, match="stream identity mismatch"):
        m.append_observation(ledger, c2)
    c3 = cockpit("2026-08-09T17:00:00Z", brief_id="b3"); c3["timeframe"] = "1h"
    with pytest.raises(ValueError, match="stream identity mismatch"):
        m.append_observation(ledger, c3)
    assert len(m.verify_ledger(ledger)) == 1


def test_32_change_from_previous_self_consistency_is_verified(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"; c = cockpit(); m.append_observation(ledger, c)
    _rewrite_record(ledger, lambda row: row.__setitem__("change_from_previous", {"material_change": True, "change_count": 99, "changes": [], "summary": "MATERIAL_CHANGE"}))
    with pytest.raises(ValueError, match="change_from_previous mismatch"):
        m.verify_ledger(ledger)


def test_33_alert_event_fields_fail_closed(tmp_path: Path) -> None:
    c = cockpit(); a = alert(c); a["events"][0]["detail"] = ""
    with pytest.raises(ValueError, match="detail"):
        m.append_observation(tmp_path / "memory.ndjson", c, a)


def test_34_partial_os_write_is_completed_under_lock(tmp_path: Path, monkeypatch) -> None:
    ledger = tmp_path / "memory.ndjson"
    real_write = m.os.write
    calls = {"n": 0}
    def short_once(fd, data):
        calls["n"] += 1
        if calls["n"] == 1 and len(data) > 10:
            return real_write(fd, data[:10])
        return real_write(fd, data)
    monkeypatch.setattr(m.os, "write", short_once)
    c = cockpit(); status, _, _ = m.append_observation(ledger, c)
    assert status == "APPENDED"
    assert calls["n"] >= 2
    assert len(m.verify_ledger(ledger)) == 1


def test_35_fsync_occurs_before_append_success(tmp_path: Path, monkeypatch) -> None:
    ledger = tmp_path / "memory.ndjson"
    real_fsync = m.os.fsync
    seen = []
    def tracking(fd):
        seen.append(fd)
        return real_fsync(fd)
    monkeypatch.setattr(m.os, "fsync", tracking)
    c = cockpit(); status, _, _ = m.append_observation(ledger, c)
    assert status == "APPENDED"
    assert len(seen) >= 2  # file + directory on first creation


def test_36_nonfinite_anywhere_in_source_packet_fails_closed(tmp_path: Path) -> None:
    c = cockpit(); c["executive"]["margin"] = float("nan")
    with pytest.raises(ValueError):
        m.append_observation(tmp_path / "memory.ndjson", c)
    c = cockpit(); c["extra_noncanonical"] = float("inf")
    with pytest.raises(ValueError):
        m.append_observation(tmp_path / "memory2.ndjson", c)


def _rewrite_rows(ledger: Path, mutate) -> None:
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    mutate(rows)
    previous = m.GENESIS
    for idx, row in enumerate(rows):
        row["sequence"] = idx + 1
        row["prev_record_hash"] = previous
        row["state_fingerprint"] = m.sha(row["state"])
        row["source_identity_fingerprint"] = m.sha(row["source_identity"])
        row["change_from_previous"] = (
            {"material_change": False, "change_count": 0, "changes": [], "summary": "BASELINE_ESTABLISHED"}
            if idx == 0 else m.diff_states(rows[idx - 1]["state"], row["state"])
        )
        body = dict(row); body.pop("record_hash", None)
        row["record_hash"] = m.sha(body)
        previous = row["record_hash"]
    ledger.write_text("\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")


def test_37_hash_consistent_malformed_persisted_state_is_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c = cockpit(); m.append_observation(ledger, c, alert(c))
    def mutate(rows):
        rows[0]["state"] = {}
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="state.*cockpit"):
        m.verify_ledger(ledger)


def test_38_hash_consistent_cross_stream_history_is_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c1 = cockpit("2026-08-09T12:00:00Z", brief_id="b1")
    c2 = cockpit("2026-08-09T13:00:00Z", brief_id="b2")
    m.append_observation(ledger, c1, alert(c1))
    m.append_observation(ledger, c2, alert(c2))
    def mutate(rows):
        rows[1]["source_identity"]["symbol"] = "ETHUSDT"
        rows[1]["source_identity"]["timeframe"] = "1h"
        rows[1]["state"]["cockpit"]["symbol"] = "ETHUSDT"
        rows[1]["state"]["cockpit"]["timeframe"] = "1h"
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="ledger stream identity mismatch"):
        m.verify_ledger(ledger)


def test_39_hash_consistent_alert_provenance_presence_mismatch_is_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c = cockpit(); m.append_observation(ledger, c, alert(c))
    def mutate(rows):
        rows[0]["source_identity"]["alert_fingerprint"] = None
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="alert provenance/state presence mismatch"):
        m.verify_ledger(ledger)


def test_40_hash_consistent_identity_state_stream_mismatch_is_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c = cockpit(); m.append_observation(ledger, c, alert(c))
    def mutate(rows):
        rows[0]["source_identity"]["symbol"] = "ETHUSDT"
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="source identity/state stream mismatch"):
        m.verify_ledger(ledger)


@pytest.mark.parametrize("field,value", [("margin", "banana"), ("regime", 123), ("grade", []), ("next", {})])
def test_41_malformed_executive_optional_fields_fail_closed(tmp_path: Path, field: str, value) -> None:
    c = cockpit(); c["executive"][field] = value
    with pytest.raises(ValueError, match="executive"):
        m.append_observation(tmp_path / "memory.ndjson", c, alert(c))


def test_42_alert_dedupe_key_format_is_enforced(tmp_path: Path) -> None:
    c = cockpit(); a = alert(c, dedupe="abc123")
    with pytest.raises(ValueError, match="dedupe_key"):
        m.append_observation(tmp_path / "memory.ndjson", c, a)


def test_43_hash_consistent_non_normalized_persisted_state_is_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c = cockpit(); m.append_observation(ledger, c, alert(c))
    def mutate(rows):
        rows[0]["state"]["cockpit"]["status"] = " READY "
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="normalized"):
        m.verify_ledger(ledger)


def test_44_hash_consistent_duplicate_persisted_lists_are_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c = cockpit(); m.append_observation(ledger, c, alert(c))
    def mutate(rows):
        rows[0]["state"]["cockpit"]["risk_flags"] = ["Relative basis extreme", "Relative basis extreme"]
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="sorted, unique and normalized"):
        m.verify_ledger(ledger)


def test_45_hash_consistent_non_normalized_source_identity_is_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "memory.ndjson"
    c = cockpit(); m.append_observation(ledger, c, alert(c))
    def mutate(rows):
        rows[0]["source_identity"]["brief_id"] = " b1 "
    _rewrite_rows(ledger, mutate)
    with pytest.raises(ValueError, match="non-normalized source_identity.brief_id"):
        m.verify_ledger(ledger)

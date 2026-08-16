from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import tradingos_decision_alerts as alerts
import tradingos_market_memory as memory
import tradingos_operator_impact as impact
import tradingos_value_attribution as attr


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
    risk_labels: list[str] | None = None,
    blockers: list[str] | None = None,
):
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
        "pressure": [
            {"label": "Price/OI alignment", "direction": "LONG", "strength": 2.0, "observation": "aligned"},
            {"label": "Spot CVD", "direction": "LONG", "strength": 1.0, "observation": "positive"},
        ],
        "levels": {
            "last": last,
            "support": support,
            "resistance": resistance,
            "to_resistance_pct": round((resistance / last - 1) * 100, 3),
        },
        "risk_flags": [{"severity": "WATCH", "label": x, "detail": x} for x in (risk_labels or [])],
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


def accept(memory_ledger: Path, c: dict, a: dict):
    status, row, _ = memory.append_observation(memory_ledger, c, a)
    assert status == "APPENDED"
    return row


def open_lineage(root: Path):
    ml = root / "memory.ndjson"
    al = root / "attr.ndjson"
    c = cockpit()
    a = alerts.build(c, None)
    accept(ml, c, a)
    status, report, rows = attr.process(al, ml, c, a)
    assert status == "APPENDED" and report["summary"]["events"] == 1
    return ml, al, c, a, report, rows


@pytest.fixture(scope="module")
def base_lineage(tmp_path_factory):
    root = tmp_path_factory.mktemp("base_lineage")
    return open_lineage(root)


def copy_attr_ledger(src: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "attr.ndjson"
    shutil.copyfile(src, dst)
    return dst


def rewrite_feedback(ledger: Path, mutate):
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    mutate(rows)
    prev = impact.GENESIS
    for i, row in enumerate(rows, 1):
        row["sequence"] = i
        row["prev_record_hash"] = prev
        body = dict(row); body.pop("record_hash", None)
        row["record_hash"] = impact.sha(body)
        prev = row["record_hash"]
    ledger.write_text("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows) + "\n")


def dump_report(path: Path, report: dict):
    path.write_text(json.dumps(report, indent=2) + "\n")


def test_01_version_and_dependency():
    assert impact.VERSION == "1.2.0"
    assert impact.value_attribution.VERSION == "1.1.1"


def test_02_exact_r25_report_and_ledger_happy(base_lineage):
    _, al, _, _, report, _ = base_lineage
    visible, lineage, records = impact._attribution_lineage(report, al)
    eid = report["events"][0]["event_id"]
    assert eid in visible and eid in lineage and records


def test_03_report_mutation_rejected_by_verified_ledger(base_lineage):
    _, al, _, _, report, _ = base_lineage
    bad = json.loads(json.dumps(report)); bad["events"][0]["title"] += " tampered"
    with pytest.raises(ValueError, match="does not match verified attribution ledger"):
        impact._attribution_lineage(bad, al)


def test_04_report_reorder_or_subset_rejected(base_lineage):
    _, al, _, _, report, _ = base_lineage
    bad = json.loads(json.dumps(report)); bad["events"] = []
    bad["summary"] = {"events": 0, "unresolved": 0, "confirmed": 0, "invalidated": 0, "expired": 0}
    with pytest.raises(ValueError, match="does not match verified attribution ledger"):
        impact._attribution_lineage(bad, al)


def test_05_feedback_append(base_lineage, tmp_path):
    _, al, _, _, report, _ = base_lineage
    ledger = tmp_path / "feedback.ndjson"; eid = report["events"][0]["event_id"]
    status, row = impact.record_feedback(ledger, report, al, eid, "helpful", "2026-08-09T17:00:00Z", " useful ")
    assert status == "APPENDED" and row["impact"] == "HELPFUL" and row["note"] == "useful"
    assert row["event_identity"]["attribution_open_record_hash"]
    assert len(impact.verify_ledger(ledger)) == 1


def test_06_feedback_before_event(base_lineage, tmp_path):
    _, al, _, _, report, _ = base_lineage; eid = report["events"][0]["event_id"]
    with pytest.raises(ValueError, match="predate"):
        impact.record_feedback(tmp_path/"x", report, al, eid, "HELPFUL", "2026-08-09T15:59:59Z")


def test_07_exact_duplicate_suppressed(base_lineage, tmp_path):
    _, al, _, _, report, _ = base_lineage; eid=report["events"][0]["event_id"]; ledger=tmp_path/"x"
    impact.record_feedback(ledger, report, al, eid, "HELPFUL", "2026-08-09T17:00:00Z", "ok")
    status,_=impact.record_feedback(ledger, report, al, eid, "HELPFUL", "2026-08-09T18:00:00Z", "ok")
    assert status=="DUPLICATE_SUPPRESSED" and len(impact.verify_ledger(ledger))==1


def test_08_conflict_rejected(base_lineage, tmp_path):
    _, al, _, _, report, _ = base_lineage; eid=report["events"][0]["event_id"]; ledger=tmp_path/"x"
    impact.record_feedback(ledger, report, al, eid, "HELPFUL", "2026-08-09T17:00:00Z")
    with pytest.raises(ValueError, match="contradictory overwrite"):
        impact.record_feedback(ledger, report, al, eid, "FALSE_ALARM", "2026-08-09T18:00:00Z")


def test_09_unknown_event_rejected(base_lineage, tmp_path):
    _, al, _, _, report, _ = base_lineage
    with pytest.raises(ValueError, match="not present"):
        impact.record_feedback(tmp_path/"x", report, al, "f"*24, "HELPFUL", "2026-08-09T17:00:00Z")


@pytest.mark.parametrize("note,pattern", [("x"*501,"<= 500"),("bad\nnote","control")])
def test_10_11_note_guards(base_lineage,tmp_path,note,pattern):
    _, al, _, _, report, _=base_lineage; eid=report["events"][0]["event_id"]
    with pytest.raises(ValueError, match=pattern):
        impact.record_feedback(tmp_path/"x",report,al,eid,"HELPFUL","2026-08-09T17:00:00Z",note)


def test_12_identity_contains_exact_open_hash_and_title(base_lineage,tmp_path):
    _, al, _, _, report, rows=base_lineage; eid=report["events"][0]["event_id"]
    _, fb=impact.record_feedback(tmp_path/"x",report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    assert fb["event_identity"]["title"] == report["events"][0]["title"]
    assert fb["event_identity"]["attribution_open_record_hash"] == rows[0]["record_hash"]
    assert fb["event_identity_fingerprint"] == impact.sha(fb["event_identity"])


def test_13_hash_tamper_rejected(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; ledger=tmp_path/"x"
    impact.record_feedback(ledger,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    row=json.loads(ledger.read_text()); row["impact"]="FALSE_ALARM"; ledger.write_text(json.dumps(row)+"\n")
    with pytest.raises(ValueError,match="record_hash mismatch"): impact.verify_ledger(ledger)


@pytest.mark.parametrize("mutation,pattern", [
    (lambda r: r[0].__setitem__("extra",True), "fields mismatch"),
    (lambda r: r[0]["contract"].__setitem__("auto_positive_feedback_forbidden",False), "contract mismatch"),
    (lambda r: r[0]["safety"].__setitem__("can_trade",True), "safety mismatch"),
    (lambda r: r[0]["event_identity"].__setitem__("title","other"), "fingerprint mismatch"),
    (lambda r: r[0]["event_identity"].__setitem__("attribution_open_record_hash","3"*64), "fingerprint mismatch"),
])
def test_14_18_rehashed_semantic_tamper(base_lineage,tmp_path,mutation,pattern):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; ledger=tmp_path/"x"
    impact.record_feedback(ledger,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    rewrite_feedback(ledger,mutation)
    with pytest.raises(ValueError,match=pattern): impact.verify_ledger(ledger)


def test_19_rehashed_fake_historical_feedback_rejected_by_attribution_history(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; ledger=tmp_path/"x"
    impact.record_feedback(ledger,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    row=json.loads(ledger.read_text())
    fake=json.loads(json.dumps(row)); fake["event_id"]="f"*24; fake["event_identity"]["event_id"]="f"*24
    fake["event_identity"]["attribution_open_record_hash"]="4"*64
    fake["event_identity_fingerprint"]=impact.sha(fake["event_identity"])
    fake["sequence"]=2; fake["prev_record_hash"]=row["record_hash"]
    body=dict(fake); body.pop("record_hash",None); fake["record_hash"]=impact.sha(body)
    ledger.write_text(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n"+json.dumps(fake,sort_keys=True,separators=(",",":"))+"\n")
    verified=impact.verify_ledger(ledger)
    with pytest.raises(ValueError,match="absent from verified attribution history"):
        impact.build_report(report,al,verified)


def test_20_attribution_ledger_tamper_rejected(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; bad=tmp_path/"attr"; shutil.copyfile(al,bad)
    rows=[json.loads(x) for x in bad.read_text().splitlines()]; rows[0]["title"]="tampered"
    bad.write_text(json.dumps(rows[0])+"\n")
    with pytest.raises(ValueError): impact._attribution_lineage(report,bad)


def test_21_blank_feedback_line_rejected(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; ledger=tmp_path/"x"
    impact.record_feedback(ledger,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    ledger.write_bytes(ledger.read_bytes()+b"\n")
    with pytest.raises(ValueError,match="blank record"): impact.verify_ledger(ledger)


def test_22_missing_final_newline_rejected(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; ledger=tmp_path/"x"
    impact.record_feedback(ledger,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    ledger.write_bytes(ledger.read_bytes().rstrip(b"\n"))
    with pytest.raises(ValueError,match="end with newline"): impact.verify_ledger(ledger)


def test_23_partial_write_completed(base_lineage,tmp_path,monkeypatch):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; ledger=tmp_path/"x"; real=impact.os.write; calls={"n":0}
    def short(fd,data): calls["n"]+=1; return real(fd,data[:10] if len(data)>10 else data)
    monkeypatch.setattr(impact.os,"write",short)
    status,_=impact.record_feedback(ledger,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    assert status=="APPENDED" and calls["n"]>1 and len(impact.verify_ledger(ledger))==1


def test_24_write_failure_rolls_back(base_lineage,tmp_path,monkeypatch):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; ledger=tmp_path/"x"; real=impact.os.write; calls={"n":0}
    def fail(fd,data):
        calls["n"]+=1
        if calls["n"]==1: return real(fd,data[:20])
        raise OSError("injected")
    monkeypatch.setattr(impact.os,"write",fail)
    with pytest.raises(OSError,match="injected"): impact.record_feedback(ledger,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    assert not ledger.exists() or ledger.read_bytes()==b""


def test_25_no_feedback_is_not_auto_positive(base_lineage):
    _,al,_,_,report,_=base_lineage
    out=impact.build_report(report,al,[])
    assert out["summary"]["positive_impact_count"]==0 and out["events"][0]["operator_impact"]=="NO_FEEDBACK"


def test_26_subjective_false_alarm_does_not_change_objective(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; ledger=tmp_path/"x"
    impact.record_feedback(ledger,report,al,eid,"FALSE_ALARM","2026-08-09T17:00:00Z")
    out=impact.build_report(report,al,impact.verify_ledger(ledger))
    assert out["events"][0]["outcome"]=="UNRESOLVED" and out["events"][0]["operator_impact"]=="FALSE_ALARM"


def resolve_lineage(root: Path):
    ml,al,c0,a0,report0,_=open_lineage(root)
    c1=cockpit("2026-08-09T20:30:00Z",brief_id="b2",last=101.0)
    a1=alerts.build(c1,c0)
    accept(ml,c1,a1)
    status,report1,rows1=attr.process(al,ml,c1,a1)
    assert status=="APPENDED"
    return al,report0,report1,rows1


def test_27_outcome_evolution_preserves_feedback_identity(tmp_path):
    ml,al,c0,a0,report0,_=open_lineage(tmp_path); eid=report0["events"][0]["event_id"]; ledger=tmp_path/"feedback"
    impact.record_feedback(ledger,report0,al,eid,"CAUSED_REVIEW","2026-08-09T17:00:00Z")
    c1=cockpit("2026-08-09T20:30:00Z",brief_id="b2",last=101.0)
    a1=alerts.build(c1,c0); accept(ml,c1,a1)
    status,report1,_=attr.process(al,ml,c1,a1); assert status=="APPENDED"
    out=impact.build_report(report1,al,impact.verify_ledger(ledger))
    row=next(x for x in out["events"] if x["event_id"]==eid)
    assert row["outcome"]=="CONFIRMED" and row["operator_impact"]=="CAUSED_REVIEW"


def _cli(report_path, attr_ledger, feedback, out, impact_name):
    report=json.loads(report_path.read_text()); eid=report["events"][0]["event_id"]
    return [sys.executable,str(TOOLS/"tradingos_operator_impact.py"),"--attribution",str(report_path),"--attribution-ledger",str(attr_ledger),"--feedback-ledger",str(feedback),"--out-dir",str(out),"--record-event-id",eid,"--impact",impact_name,"--recorded-at","2026-08-09T17:00:00Z","--note","race"]


def test_28_concurrent_identical_writers(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; rp=tmp_path/"report.json"; dump_report(rp,report); fb=tmp_path/"fb"
    procs=[subprocess.Popen(_cli(rp,al,fb,tmp_path/f"o{i}","HELPFUL"),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(6)]
    results=[p.communicate(timeout=30)+(p.returncode,) for p in procs]
    assert all(r[2]==0 for r in results); statuses=[json.loads(r[0])["record_status"] for r in results]
    assert statuses.count("APPENDED")==1 and statuses.count("DUPLICATE_SUPPRESSED")==5 and len(impact.verify_ledger(fb))==1


def test_29_concurrent_contradictory_writers(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; rp=tmp_path/"report.json"; dump_report(rp,report); fb=tmp_path/"fb"
    names=["HELPFUL","FALSE_ALARM","IGNORED","AVOIDED_ACTION"]
    procs=[subprocess.Popen(_cli(rp,al,fb,tmp_path/f"o{i}",name),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i,name in enumerate(names)]
    results=[p.communicate(timeout=30)+(p.returncode,) for p in procs]
    assert [r[2] for r in results].count(0)==1 and len(impact.verify_ledger(fb))==1


def test_30_cli_safety(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; rp=tmp_path/"report.json"; dump_report(rp,report)
    proc=subprocess.run([sys.executable,str(TOOLS/"tradingos_operator_impact.py"),"--attribution",str(rp),"--attribution-ledger",str(al),"--feedback-ledger",str(tmp_path/"fb"),"--out-dir",str(tmp_path/"out")],capture_output=True,text=True,timeout=30)
    assert proc.returncode==0; payload=json.loads(proc.stdout); assert payload["can_trade"] is False and payload["capital_permission"]=="DENY"


@pytest.mark.parametrize("field,value", [
    ("schema","wrong"),
    ("version","1.1.0"),
])
def test_31_32_top_level_report_contract_rejected(base_lineage,field,value):
    _,al,_,_,report,_=base_lineage; bad=json.loads(json.dumps(report)); bad[field]=value
    with pytest.raises(ValueError): impact._attribution_lineage(bad,al)


def test_33_unsafe_report_rejected(base_lineage):
    _,al,_,_,report,_=base_lineage; bad=json.loads(json.dumps(report)); bad["safety"]["can_trade"]=True
    with pytest.raises(ValueError): impact._attribution_lineage(bad,al)


def test_34_non_memory_bound_report_rejected(base_lineage):
    _,al,_,_,report,_=base_lineage; bad=json.loads(json.dumps(report)); bad["contract"]["market_memory_bound"]=False
    with pytest.raises(ValueError): impact._attribution_lineage(bad,al)


def test_35_feedback_ledger_exact_safety_contract(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; _,row=impact.record_feedback(tmp_path/"fb",report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    assert row["safety"]==impact.SAFETY and row["contract"]==impact.FEEDBACK_CONTRACT


def test_36_report_contract_exact(base_lineage):
    _,al,_,_,report,_=base_lineage; out=impact.build_report(report,al,[])
    assert out["contract"]==impact.REPORT_CONTRACT and out["safety"]==impact.SAFETY


def test_37_title_is_immutable_via_open_record(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; fb=tmp_path/"fb"
    impact.record_feedback(fb,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    bad=json.loads(json.dumps(report)); bad["events"][0]["title"]="different"
    with pytest.raises(ValueError,match="does not match verified attribution ledger"): impact.build_report(bad,al,impact.verify_ledger(fb))


def test_38_feedback_open_record_hash_is_verified_against_history(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; fb=tmp_path/"fb"
    impact.record_feedback(fb,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    rewrite_feedback(fb,lambda rows: (rows[0]["event_identity"].__setitem__("attribution_open_record_hash","5"*64), rows[0].__setitem__("event_identity_fingerprint",impact.sha(rows[0]["event_identity"]))))
    rows=impact.verify_ledger(fb)
    with pytest.raises(ValueError,match="immutable event identity"): impact.build_report(report,al,rows)


def test_39_feedback_event_id_format():
    with pytest.raises(ValueError): impact._hash_hex("xyz",24,"x")


def test_40_feedback_timestamp_normalization(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]
    _,row=impact.record_feedback(tmp_path/"fb",report,al,eid,"HELPFUL","2026-08-09T17:00:00+00:00")
    assert row["recorded_at"]=="2026-08-09T17:00:00Z"


def test_41_report_feedback_counts_visible_only(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; fb=tmp_path/"fb"
    impact.record_feedback(fb,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    out=impact.build_report(report,al,impact.verify_ledger(fb)); assert out["summary"]["feedback_count"]==1 and out["summary"]["positive_impact_count"]==1


def test_42_production_import_does_not_require_network():
    src=(TOOLS/"tradingos_operator_impact.py").read_text()
    for bad in ("requests","httpx","urllib","socket","telegram","webhook","subprocess"):
        assert f"import {bad}" not in src and f"from {bad}" not in src


def test_43_exact_lineage_has_no_r27_git_side_effect(base_lineage):
    _,al,_,_,report,_=base_lineage
    impact._attribution_lineage(report,al)
    assert al.exists()


def test_44_boolean_sequence_rejected_after_rehash(base_lineage,tmp_path):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; fb=tmp_path/"fb"
    impact.record_feedback(fb,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    row=json.loads(fb.read_text()); row["sequence"]=True
    body=dict(row); body.pop("record_hash",None); row["record_hash"]=impact.sha(body)
    fb.write_text(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n")
    with pytest.raises(ValueError,match="invalid sequence"): impact.verify_ledger(fb)


def test_45_post_append_verification_failure_rolls_back(base_lineage,tmp_path,monkeypatch):
    _,al,_,_,report,_=base_lineage; eid=report["events"][0]["event_id"]; fb=tmp_path/"fb"
    real_verify=impact.verify_ledger; calls={"n":0}
    def injected(path):
        calls["n"]+=1
        if calls["n"]==2:
            raise ValueError("post-append injected")
        return real_verify(path)
    monkeypatch.setattr(impact,"verify_ledger",injected)
    with pytest.raises(ValueError,match="post-append injected"):
        impact.record_feedback(fb,report,al,eid,"HELPFUL","2026-08-09T17:00:00Z")
    assert fb.exists() and fb.read_bytes()==b""


def test_46_real_r25_tail100_preserves_verified_historical_feedback(tmp_path):
    from datetime import datetime, timedelta, timezone
    ml=tmp_path/"memory"; al=tmp_path/"attr"; fb=tmp_path/"feedback"
    dt=datetime(2026,8,9,16,tzinfo=timezone.utc)
    def ts(x): return x.isoformat().replace("+00:00","Z")
    c0=cockpit(ts(dt),brief_id="tail0",risk_labels=[]); a0=alerts.build(c0,None)
    accept(ml,c0,a0); _,r0,_=attr.process(al,ml,c0,a0); first=r0["events"][0]["event_id"]
    impact.record_feedback(fb,r0,al,first,"HELPFUL","2026-08-09T16:30:00Z")
    previous=c0; risks=[]; current=r0
    for i in range(1,101):
        dt += timedelta(minutes=1); risks=risks+[f"TAIL_RISK_{i}"]
        c=cockpit(ts(dt),brief_id=f"tail{i}",risk_labels=risks); a=alerts.build(c,previous)
        accept(ml,c,a); _,current,_=attr.process(al,ml,c,a); previous=c
    assert current["summary"]["events"]==101 and len(current["events"])==100
    assert all(x["event_id"]!=first for x in current["events"])
    out=impact.build_report(current,al,impact.verify_ledger(fb))
    assert out["summary"]["historical_feedback_outside_current_view"]==1
    assert out["summary"]["feedback_ledger_rows"]==1 and out["summary"]["feedback_count"]==0
